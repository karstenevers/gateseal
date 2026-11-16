import contextlib
import dataclasses
import os
import re
import stat
import tempfile
from pathlib import Path

import pytest
from installation import (
    clone,
    install,
    next_release,
    package_version,
    process,
    success,
    versioned_package,
)

TRACKED = ("gateseal.toml", "gateseal.lock", ".pre-commit-config.yaml")


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
    keeps the developer's cache and Pythons.
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
    os.environ.setdefault("GATESEAL_INTERPRETER_STORE", str(home / "interpreter store"))
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME"):
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
def installed(tmp_path_factory):
    """Install the wheel, and set up a home of its own as gateseal setup does."""
    base = tmp_path_factory.mktemp("installed wheel ü").resolve()
    with pytest.MonkeyPatch.context() as patch:
        for name, value in home_environment(base / "home ü").items():
            patch.setenv(name, value)
        make_home(Path.home())
        installation = install(base)
        installation.cli(Path.home(), "setup")
    return installation


@pytest.fixture(scope="session")
def next_installed(installed, tmp_path_factory):
    """Install the next release, a second compiler sharing installed's uv cache."""
    directory = tmp_path_factory.mktemp("next wheel ü").resolve()
    package = versioned_package(directory / "package", next_release(package_version()))
    return dataclasses.replace(
        install(directory, package), environment=installed.environment
    )


def adopt(installation, root):
    """Configure root with installation's compiler and commit its files."""
    installation.cli(
        root,
        "configure",
        "--capability",
        "hygiene",
        "--compiler-wheel",
        str(installation.wheel),
        "--compiler-sha256",
        installation.checksum,
    )
    installation.cli(root, "setup", "--refresh")
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "-m", "Adopt hygiene"], root)
    return root


@pytest.fixture
def consumer(tmp_path):
    return clone(tmp_path / "origin", tmp_path / "consumer space ü ")


@pytest.fixture
def configured(installed, consumer):
    installed.cli(
        consumer,
        "configure",
        "--capability",
        "hygiene",
        "--compiler-wheel",
        str(installed.wheel),
        "--compiler-sha256",
        installed.checksum,
    )
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
    installed.cli(
        first,
        "configure",
        "--capability",
        "hygiene",
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
    )
    success(["git", "add", *TRACKED], first)
    success(
        ["git", "-c", "user.name=First", "-c", "user.email=first@example.invalid"]
        + ["commit", "-m", "Adopt hygiene"],
        first,
    )
    success(["git", "push", "origin", "main"], first)
    return origin
