"""Build and install the wheel, and make consumer clones, outside pytest.

conftest's fixtures and other drivers of the installed command share this code.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from gateseal.setup import python_request

PACKAGE = Path(__file__).resolve().parents[1]


def process(arguments, cwd, env=None):
    return subprocess.run(
        [str(a) for a in arguments], cwd=cwd, env=env, capture_output=True, text=True
    )


def success(arguments, cwd, env=None):
    result = process(arguments, cwd, env)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@dataclass
class Installation:
    wheel: Path
    checksum: str
    python: Path
    command: Path
    environment: dict[str, str] = field(repr=False)

    def cli(self, root, *arguments, code=0, env=None):
        result = process(
            [self.command, "--repo", root, "--json", *arguments],
            root,
            env or self.environment,
        )
        assert result.returncode == code, result.stdout + result.stderr
        document = json.loads(result.stdout)
        assert document["schema"] == 1
        return document


def environment(directory):
    """Return the environment whose uv and mise caches are directory's."""
    result = dict(
        os.environ,
        UV_CACHE_DIR=str(directory / "uv-cache"),
        MISE_CACHE_DIR=str(directory / "mise-cache"),
    )
    result.pop("PYTHONPATH", None)
    return result


def share_uv_pythons(home):
    """Let uv in home use the Pythons in UV_PYTHON_INSTALL_DIR.

    gateseal's uv ignores that variable and looks in home's own folder, so
    without this link setup in each new home installs a Python of its own.
    The Python that setup asks for is installed in the shared folder first, so
    uv never installs it through the link and records the home's path in it.
    """
    shared = Path(os.environ["UV_PYTHON_INSTALL_DIR"]).resolve()
    success(
        ["uv", "python", "install", "--no-bin", python_request()],
        home,
        dict(os.environ, UV_PYTHON_INSTALL_DIR=str(shared)),
    )
    env = dict(os.environ, HOME=str(home))
    del env["UV_PYTHON_INSTALL_DIR"]
    folder = Path(success(["uv", "python", "dir"], home, env).stdout.strip())
    folder.parent.mkdir(parents=True, exist_ok=True)
    folder.symlink_to(shared, target_is_directory=True)


def install(directory, package=PACKAGE):
    """Build package's wheel and install it with its hash into directory."""
    env = environment(directory)
    success(
        [
            "uv",
            "build",
            "--no-config",
            "--wheel",
            "--python",
            sys.executable,
            "--out-dir",
            directory / "dist",
            package,
        ],
        directory,
        env,
    )
    wheel = next((directory / "dist").glob("*.whl"))
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
    destination = directory / "tool environment"
    success(
        [
            "uv",
            "venv",
            "--no-config",
            "--no-python-downloads",
            "--python",
            sys.executable,
            destination,
        ],
        directory,
        env,
    )
    python = Path(
        success(
            [
                "uv",
                "python",
                "find",
                "--no-config",
                "--no-python-downloads",
                destination,
            ],
            directory,
            env,
        ).stdout.strip()
    )
    requirements = directory / "compiler.txt"
    requirements.write_text(f"gateseal @ {wheel.as_uri()} --hash=sha256:{checksum}\n")
    success(
        [
            "uv",
            "pip",
            "install",
            "--no-config",
            "--python",
            python,
            "--require-hashes",
            "--no-deps",
            "-r",
            requirements,
        ],
        directory,
        env,
    )
    install_coverage(python, directory)
    scripts = success(
        [
            python,
            "-I",
            "-B",
            "-c",
            "import sysconfig; print(sysconfig.get_path('scripts'))",
        ],
        directory,
        env,
    ).stdout.strip()
    return Installation(wheel, checksum, python, Path(scripts) / "gateseal", env)


def install_coverage(python, directory):
    """Let coverage measure the installed copy when the suite runs under coverage.

    coverage's subprocess patch starts measuring in any interpreter that can
    import coverage, so the tool environment gets the coverage release that
    uv.lock pins, checked against its hashes. uv may read the package index:
    on a fresh runner the cache holds the wheel but not the index page an
    offline install resolves from.
    """
    if "COVERAGE_PROCESS_CONFIG" not in os.environ:
        return
    import tomllib

    lock = tomllib.loads((PACKAGE / "uv.lock").read_text())
    coverage = next(entry for entry in lock["package"] if entry["name"] == "coverage")
    pins = [f"--hash={wheel['hash']}" for wheel in coverage["wheels"]]
    requirements = directory / "coverage-requirements.txt"
    requirements.write_text(" ".join([f"coverage=={coverage['version']}", *pins]))
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    success(
        ["uv", "pip", "install", "--no-config", "--require-hashes", "--no-deps"]
        + ["--python", python, "-r", requirements],
        directory,
        env,
    )


def package_version(package=PACKAGE):
    """Return the version in package's pyproject.toml."""
    import tomllib

    project = tomllib.loads((package / "pyproject.toml").read_text())["project"]
    return project["version"]


def next_release(version):
    """Return version with its last component one higher: 0.1.0 becomes 0.1.1."""
    *head, last = version.split(".")
    return ".".join([*head, str(int(last) + 1)])


def versioned_package(destination, version, package=PACKAGE):
    """Copy package's build inputs to destination with version as its release.

    The wheel built from the copy is a second genuine compiler, with its own
    filename and SHA-256, whose compiler accepts only Locks that name version.
    Its bundled policy is the package's.
    """
    shutil.copytree(
        package / "src",
        destination / "src",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in ("README.md", "LICENSE", ".pre-commit-hooks.yaml"):
        shutil.copy(package / name, destination / name)
    for name, key in (
        ("pyproject.toml", "version"),
        ("src/gateseal/__init__.py", "__version__"),
    ):
        text, count = re.subn(
            rf'^{key} = "[^"]*"$',
            f'{key} = "{version}"',
            (package / name).read_text(),
            flags=re.MULTILINE,
        )
        assert count == 1, f"{name} must hold exactly one {key}"
        (destination / name).write_text(text)
    return destination


BUILD_INPUTS = (
    "src",
    "README.md",
    "pyproject.toml",
    "LICENSE",
    ".pre-commit-hooks.yaml",
)
"""The files of a package that its wheel is built from, and its hook definition."""


def tag_release(origin, version, package=PACKAGE):
    """Commit package's build inputs in origin, tag them v<version>, return the commit.

    origin stands in for gateseal's repository, which a test's Git reaches
    through url.<base>.insteadOf, so configure reads the release's commit from
    its tag there and pre-commit and CI can build gateseal at that commit.
    """
    if not origin.exists():
        origin.mkdir(parents=True)
        success(["git", "init", "--initial-branch=main"], origin)
    for name in BUILD_INPUTS:
        target = origin / name
        if target.is_dir():
            shutil.rmtree(target)
        if (package / name).is_dir():
            shutil.copytree(
                package / name, target, ignore=shutil.ignore_patterns("__pycache__")
            )
        elif (package / name).exists():
            shutil.copy(package / name, target)
    identity = ["-c", "user.name=Release", "-c", "user.email=r@example.invalid"]
    success(["git", "add", "-A"], origin)
    success(["git", *identity, "commit", "-q", "-m", f"Release {version}"], origin)
    success(["git", "tag", f"v{version}"], origin)
    return success(["git", "rev-parse", "HEAD"], origin).stdout.strip()


def clone(origin, root):
    """Clone a new empty origin to root, with a commit identity."""
    origin.mkdir(parents=True)
    success(["git", "init", "--initial-branch=main"], origin)
    success(["git", "clone", origin, root], origin.parent)
    success(["git", "config", "user.name", "gateseal test"], root)
    success(["git", "config", "user.email", "gateseal@example.invalid"], root)
    return root
