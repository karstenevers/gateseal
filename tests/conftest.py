import contextlib
import dataclasses
import json
import os
import re
import shutil
import socket
import stat
import tempfile
from pathlib import Path

import pytest
from gateseal.tools import built_in_tools
from gateseal.tools.pre_commit_repository import PreCommitRepository
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


def remove_file(path):
    """Delete path from its folder, even a read-only one."""
    parent = path.parent.stat()
    path.parent.chmod(stat.S_IMODE(parent.st_mode) | stat.S_IWUSR)
    path.unlink()
    path.parent.chmod(stat.S_IMODE(parent.st_mode))


def disguise(path, text):
    """Replace path's bytes with text, keeping its size, mode and mtime.

    Commits compare a store entry's metadata, so only such an edit of a hook
    environment reaches pre-commit. A comment line pads text to the size.
    """
    data = text.encode()
    padding = path.stat().st_size - len(data) - 2
    assert padding >= 0, f"{text!r} is longer than {path}"
    overwrite(path, data + b"#" * (padding + 1) + b"\n")


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


def published(root):
    local = git_path(root, "gateseal")
    return local / os.readlink(local / "current")


def snapshot(root):
    """Return what another activation must not change in root's preparation."""
    local = git_path(root, "gateseal")
    return (
        published(root).name,
        sorted(entry.name for entry in local.iterdir()),
        (published(root) / "prepared.json").read_bytes(),
    )


def prepared_state(root):
    return json.loads((published(root) / "prepared.json").read_bytes())


def recorded_tree(state, name):
    """Return the recorded tree that holds paths[name], such as interpreter."""
    path = Path(state["paths"][name])
    return next(Path(tree) for tree in state["trees"] if path.is_relative_to(tree))


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


HOOK_REPOSITORIES = tuple(
    tool.source.repository
    for tool in built_in_tools().values()
    if isinstance(tool.source, PreCommitRepository)
)


def pytest_configure(config):
    """Keep the interpreter store under pytest's temporary root.

    --basetemp or PYTEST_DEBUG_TEMPROOT chooses that root, and xdist workers
    inherit the variable, so no test reaches the user's real store.
    """
    root = config.option.basetemp or os.environ.get(
        "PYTEST_DEBUG_TEMPROOT", tempfile.gettempdir()
    )
    store = Path(root).resolve() / "gateseal interpreter store"
    os.environ.setdefault("GATESEAL_INTERPRETER_STORE", str(store))


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


def mirrored(environment, directory):
    """Return environment with Git cloning the hook repositories from mirrors.

    Activation clones each hook repository once per interpreter store, into
    its hook entry, from mirrors in directory instead of GitHub.
    """
    settings = {}
    for url in HOOK_REPOSITORIES:
        mirror = directory / url.rsplit("/", 1)[1]
        success(["git", "clone", "--mirror", "--quiet", url, mirror], directory)
        settings[f"url.{mirror.as_uri()}.insteadOf"] = url
    return with_git_config(environment, settings)


@pytest.fixture(scope="session")
def installed(tmp_path_factory):
    installation = install(tmp_path_factory.mktemp("installed wheel ü").resolve())
    environment = mirrored(
        installation.environment, tmp_path_factory.mktemp("hook mirrors")
    )
    return dataclasses.replace(installation, environment=environment)


@pytest.fixture(scope="session")
def next_installed(installed, tmp_path_factory):
    """Install the next release, a second compiler sharing installed's uv cache."""
    directory = tmp_path_factory.mktemp("next wheel ü").resolve()
    package = versioned_package(directory / "package", next_release(package_version()))
    return dataclasses.replace(
        install(directory, package), environment=installed.environment
    )


def adopt(installation, root):
    """Configure root with installation's compiler, activate it and commit."""
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
    installation.cli(root, "activate")
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
    return adopt(installed, clone(root / "origin", root / "consumer space ü "))


@pytest.fixture
def adopted(installed, configured):
    (configured / "notes.txt").write_bytes(b"original\n")
    installed.cli(configured, "activate")
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


INTERRUPT = """
import os
import sys
from gateseal import runtime
from gateseal.cli import main
step = sys.argv[1]
replace, write = os.replace, runtime.write_atomic
def interrupted_replace(source, destination, **options):
    if step == "publish" and destination == "current":
        os._exit(90)
    return replace(source, destination, **options)
def interrupted_write(path, content, mode=0o600):
    if step == "record" and path.name == "prepared.json":
        os._exit(90)
    return write(path, content, mode)
os.replace = interrupted_replace
runtime.write_atomic = interrupted_write
raise SystemExit(main(["--repo", os.getcwd(), "activate"]))
"""


def interrupt_activation(installed, root, step):
    result = process(
        [installed.python, "-I", "-B", "-c", INTERRUPT, step],
        root,
        installed.environment,
    )
    assert result.returncode == 90, result.stdout + result.stderr


def attempts(root):
    return {path.name for path in git_path(root, "gateseal").glob("attempt-*")}


def unreachable(environment):
    """Return environment with every HTTPS connection refused before it leaves."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        proxy = f"http://127.0.0.1:{probe.getsockname()[1]}"
    return dict(
        environment, HTTPS_PROXY=proxy, https_proxy=proxy, NO_PROXY="", no_proxy=""
    )


def uv_shim(installed, tmp_path, python_install):
    """Return an environment whose uv runs python_install for `uv python install`.

    The environment has an interpreter store of its own, so activation installs
    the interpreter instead of reusing installed's.
    """
    shims = tmp_path / "uv shim"
    shims.mkdir(exist_ok=True)
    real = shutil.which("uv", path=installed.environment["PATH"])
    (shims / "uv").write_text(
        '#!/bin/sh\nif [ "$1 $2" = "python install" ]; then\n  shift 2\n'
        f'  {python_install.format(uv=real)}\nfi\nexec {real!r} "$@"\n'
    )
    (shims / "uv").chmod(0o755)
    path = os.pathsep.join([str(shims), installed.environment["PATH"]])
    store = str(shims / "interpreter store")
    return dict(installed.environment, PATH=path, GATESEAL_INTERPRETER_STORE=store)
