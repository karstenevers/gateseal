import contextlib
import dataclasses
import json
import os
import re
import stat
import tempfile
from pathlib import Path

import pytest
from gateseal import __version__, lock
from gateseal.compiler_release import REPOSITORY, GatesealPin
from gateseal.policy import load_bundled_policy
from installation import (
    clone,
    install,
    next_release,
    package_version,
    process,
    success,
    tag_release,
    versioned_package,
)

TRACKED = ("gateseal.toml", "gateseal.lock", ".pre-commit-config.yaml")

PIN = GatesealPin(REPOSITORY, __version__, "5ea1" + "0" * 36)
"""The gateseal release that the tests run in this process pin."""
BUNDLED_MINIMUM = f'minimum_compiler = "{load_bundled_policy().minimum_compiler}"'
"""The bundled policy's minimum_compiler line, which tests edit into variants."""
BUNDLED_TOOLS = load_bundled_policy().tools
"""The bundled policy's tools by name, each with its pin and reviewed versions."""


def reviewed_line(name):
    """Return the bundled policy.toml's reviewed line of tool name."""
    return f"reviewed = {json.dumps(list(BUNDLED_TOOLS[name].versions))}"


def newer_release(name):
    """Return a release of tool name above every version the bundled policy reviews."""
    newest = max(
        BUNDLED_TOOLS[name].versions, key=lambda v: tuple(map(int, v.split(".")))
    )
    major, minor, _ = newest.split(".")
    return f"{major}.{int(minor) + 1}.0"


def overwrite(path, data, info=None):
    """Write data to path, even a read-only one, with info's mode and mtime.

    info defaults to path's own status, so only the bytes change.
    """
    info = info or path.stat()
    path.chmod(stat.S_IMODE(info.st_mode) | stat.S_IWUSR)
    path.write_bytes(data)
    path.chmod(stat.S_IMODE(info.st_mode))
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))


@contextlib.contextmanager
def preserved(path):
    data, info = path.read_bytes(), path.stat()
    try:
        yield data
    finally:
        overwrite(path, data, info)


def stderr_refusal(err):
    """Return the message and next step of a commit-time refusal's two lines."""
    shown = re.fullmatch(r"gateseal: (.+)\nNext: (.+)\n", err)
    assert shown, "a refusal prints one gateseal: line and one Next: line"
    return shown.groups()


def named_flags(text):
    """Return the command-line flags that text names."""
    return {word.strip(",.;") for word in text.split() if word.startswith("--")}


def git_path(root, name):
    """Return the path Git gives name in root's own Git directory."""
    arguments = ["git", "rev-parse", "--path-format=absolute", "--git-path", name]
    return Path(success(arguments, root).stdout.removesuffix("\n"))


def commit_hygiene(root, env=None):
    """Commit a file with trailing whitespace: the hook fixes it, the fix commits."""
    bad = root / "hygiene.txt"
    bad.write_bytes(b"trailing space  \n")
    success(["git", "add", bad.name], root)
    failed = process(["/usr/bin/git", "commit", "-m", "Bad hygiene"], root, env)
    assert failed.returncode != 0, failed.stdout + failed.stderr
    assert bad.read_bytes() == b"trailing space\n"
    success(["git", "add", bad.name], root)
    success(["/usr/bin/git", "commit", "-m", "Fixed hygiene"], root, env)


MACHINE = ("HOME", "GIT_CONFIG_GLOBAL", "PRE_COMMIT_HOME")
"""The variables that name a home, its global Git configuration and its hooks."""


def home_environment(home):
    return dict(
        HOME=str(home),
        GIT_CONFIG_GLOBAL=str(home / ".gitconfig"),
        PRE_COMMIT_HOME=str(home / "pre-commit home"),
    )


def pytest_configure(config):
    """Set up the environment that every test inherits.

    Every test process gets an empty home under pytest's temporary root, which
    --basetemp or PYTEST_DEBUG_TEMPROOT chooses, so no test reads or writes
    the developer's Git configuration, pre-commit or mise. gateseal setup never
    runs in this home. The installed fixture makes a second home for it. uv
    keeps the developer's cache and Pythons. UV_PROJECT_ENVIRONMENT is unset,
    so uv installs each test project in its own .venv. GITHUB_ACTIONS and
    TF_BUILD are unset, so text output prints a warning as Warning: even when a
    CI provider runs the tests.
    """
    root = config.option.basetemp or os.environ.get(
        "PYTEST_DEBUG_TEMPROOT", tempfile.gettempdir()
    )
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    home = Path(root).resolve() / f"gateseal home {worker}"
    kept = {"UV_CACHE_DIR": "cache", "UV_PYTHON_INSTALL_DIR": "python"}
    for name, command in kept.items():
        if name not in os.environ:
            os.environ[name] = success(
                ["uv", command, "dir"], Path.cwd()
            ).stdout.strip()
    for name in (
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "UV_PROJECT_ENVIRONMENT",
        "GITHUB_ACTIONS",
        "TF_BUILD",
    ):
        os.environ.pop(name, None)
    os.environ.update(home_environment(home))


def make_home(home):
    """Make home, where Git runs no background maintenance.

    A fixture that copies a repository right after a commit then sees only
    files that stay.
    """
    home.mkdir(parents=True, exist_ok=True)
    success(["git", "config", "--global", "maintenance.auto", "false"], home)


@pytest.fixture(scope="session", autouse=True)
def home(tmp_path_factory):
    """Make the home that pytest_configure names, once pytest emptied its root."""
    tmp_path_factory.getbasetemp()
    make_home(Path.home())


@contextlib.contextmanager
def on_machine(installation):
    """Run the block in installation's home, where gateseal setup ran."""
    with pytest.MonkeyPatch.context() as patch:
        for name in MACHINE:
            patch.setenv(name, installation.environment[name])
        yield


@pytest.fixture(scope="session", autouse=True)
def release_tag():
    """Let configure and upgrade in this process pin PIN without reading a tag.

    Session scope covers the fixtures of every scope that configure. The
    installed command reads its tag from gateseal_origin instead.
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(lock, "release_pin", lambda git, preparation: PIN)
        yield


@pytest.fixture(autouse=True)
def machine_home(request):
    """Give a test of the installed command the home that gateseal setup ran in.

    Its Git then copies the commit launcher into each new clone, and a commit
    runs the pre-commit of that home.
    """
    if "installed" not in request.fixturenames:
        yield
        return
    with on_machine(request.getfixturevalue("installed")):
        yield


def pytest_collection_modifyitems(items):
    for item in items:
        if "installed" in item.fixturenames:
            item.add_marker(pytest.mark.integration)


def with_git_config(environment, settings):
    """Return environment with settings after its own GIT_CONFIG_* entries."""
    count = int(environment.get("GIT_CONFIG_COUNT", "0"))
    result = dict(environment, GIT_CONFIG_COUNT=str(count + len(settings)))
    for index, (key, value) in enumerate(settings.items(), count):
        result[f"GIT_CONFIG_KEY_{index}"] = key
        result[f"GIT_CONFIG_VALUE_{index}"] = value
    return result


@pytest.fixture(scope="session")
def gateseal_origin(tmp_path_factory):
    """Return the stand-in for gateseal's repository, with this release's tag."""
    origin = tmp_path_factory.mktemp("gateseal origin").resolve() / "gateseal"
    tag_release(origin, package_version())
    return origin


@pytest.fixture(scope="session")
def installed(gateseal_origin, tmp_path_factory):
    """Install the wheel, and set up a home of its own as gateseal setup does.

    The home's Git reaches gateseal's repository at gateseal_origin.
    """
    base = tmp_path_factory.mktemp("installed wheel ü").resolve()
    with pytest.MonkeyPatch.context() as patch:
        for name, value in home_environment(base / "home ü").items():
            patch.setenv(name, value)
        make_home(Path.home())
        success(
            ["git", "config", "--global"]
            + [f"url.{gateseal_origin.as_uri()}.insteadOf", REPOSITORY],
            Path.home(),
        )
        installation = install(base)
        installation.cli(Path.home(), "setup")
    return installation


@pytest.fixture(scope="session")
def next_installed(installed, gateseal_origin, tmp_path_factory):
    """Install the next release, a second gateseal sharing installed's uv cache.

    gateseal_origin gets its release tag too.
    """
    directory = tmp_path_factory.mktemp("next wheel ü").resolve()
    version = next_release(package_version())
    package = versioned_package(directory / "package", version)
    tag_release(gateseal_origin, version, package)
    return dataclasses.replace(
        install(directory, package), environment=installed.environment
    )


def adopt(installation, root):
    """Configure root with installation's gateseal and commit its files."""
    installation.cli(root, "configure", "--capability", "hygiene")
    installation.cli(root, "setup", "--refresh")
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "-m", "Adopt hygiene"], root)
    return root


@pytest.fixture
def consumer(tmp_path):
    return clone(tmp_path / "origin", tmp_path / "consumer space ü ")


@pytest.fixture
def configured(installed, consumer):
    installed.cli(consumer, "configure", "--capability", "hygiene")
    return consumer


@pytest.fixture(scope="module")
def prepared(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("prepared consumer ü").resolve()
    with on_machine(installed):
        return adopt(installed, clone(root / "origin", root / "consumer space ü "))


@pytest.fixture
def adopted(installed, configured):
    (configured / "notes.txt").write_bytes(b"original\n")
    installed.cli(configured, "setup", "--refresh")
    success(["git", "add", *TRACKED, "notes.txt"], configured)
    success(["git", "commit", "-m", "Adopt hygiene"], configured)
    return configured


def published_origin(installed, tmp_path):
    origin = tmp_path / "origin.git"
    success(["git", "init", "--bare", "--initial-branch=main", origin], tmp_path)
    first = tmp_path / "first"
    success(["git", "clone", origin, first], tmp_path)
    installed.cli(first, "configure", "--capability", "hygiene")
    success(["git", "add", *TRACKED], first)
    success(
        ["git", "-c", "user.name=First", "-c", "user.email=first@example.invalid"]
        + ["commit", "-m", "Adopt hygiene"],
        first,
    )
    success(["git", "push", "origin", "main"], first)
    return origin
