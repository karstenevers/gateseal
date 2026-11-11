"""Acquire an interpreter with a uv whose download reads a local archive.

`uv python install` downloads CPython, and `uv python find` runs the
interpreter it finds, so the uv here installs a small local file standing in
for the archive and finds what it installed.
"""

import hashlib
import shlex
import sys

import pytest
from gateseal import interpreter_store
from gateseal.diagnostics import GatesealError

KEY = "cpython-3.14.0-linux-x86_64-gnu"
PYTHON = b"#!/bin/sh\necho 'Python 3.14.0'\n"
SHA256 = hashlib.sha256(PYTHON).hexdigest()
FAKE_UV = r"""
import hashlib
import json
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def option(name):
    return arguments[arguments.index(name) + 1]


command, arguments = sys.argv[1:3], sys.argv[3:]
with open(os.environ["FAKE_UV_LOG"], "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\n")
if command == ["python", "install"]:
    key = arguments[-1]
    downloads = Path(option("--python-downloads-json-url"))
    record = json.loads(downloads.read_bytes())[key]
    try:
        archive = Path(unquote(urlsplit(record["url"]).path)).read_bytes()
    except FileNotFoundError:
        sys.exit(
            f"error: Failed to install {key}\n"
            f"  Caused by: error sending request for url ({record['url']})"
        )
    if hashlib.sha256(archive).hexdigest() != record["sha256"]:
        sys.exit(f"error: Hash mismatch for `{key}`")
    python = Path(option("--install-dir")) / key / "bin" / "python3.14"
    python.parent.mkdir(parents=True)
    python.write_bytes(archive)
    python.chmod(0o755)
elif command == ["python", "find"]:
    request = Path(arguments[-1])
    if request.is_absolute():
        found = request / "bin" / "python"
    else:
        installed = Path(os.environ["UV_PYTHON_INSTALL_DIR"]) / request
        found = Path(os.environ.get("FAKE_UV_FOUND", installed / "bin" / "python3.14"))
    if not found.is_file():
        sys.exit(f"error: No interpreter found for {request}")
    print(found)
else:
    sys.exit(f"fake uv cannot run {sys.argv[1:]}")
"""


def python_program(path, source):
    """Make path a program that runs source on this suite's Python."""
    script = path.with_suffix(".py")
    script.write_text(source)
    path.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I "
        f'{shlex.quote(str(script))} "$@"\n'
    )
    path.chmod(0o755)
    return path


def fake_uv(directory):
    """Return a uv that installs local archives and finds what it installed.

    Each run appends its arguments to the file that FAKE_UV_LOG names.
    """
    directory.mkdir(exist_ok=True)
    return python_program(directory / "uv", FAKE_UV)


@pytest.fixture
def store(monkeypatch, tmp_path):
    store = tmp_path.resolve() / "store"
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", str(store))
    return store


@pytest.fixture
def uv(tmp_path):
    return fake_uv(tmp_path / "fake uv")


def archive_path(tmp_path):
    return tmp_path.resolve() / "archives" / "cpython 3.14.0.tar.gz"


def archive(tmp_path, saved=True):
    """Return a local archive's metadata, the archive saved unless saved is false."""
    path = archive_path(tmp_path)
    if saved:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(PYTHON)
    record = {"name": "cpython", "url": path.as_uri(), "sha256": SHA256}
    return interpreter_store.Archive({KEY: record})


def attempt(tmp_path, digit):
    path = tmp_path / "gateseal" / ("attempt-" + digit * 32)
    path.mkdir(parents=True)
    return path


def installs(tmp_path):
    log = (tmp_path / "uv.log").read_text().splitlines()
    return sum(line.startswith("python install ") for line in log)


def acquire(tmp_path, uv, attempt, source=None, **variables):
    environment = {
        "UV_PYTHON_CACHE_DIR": str(tmp_path / "python archives"),
        "FAKE_UV_LOG": str(tmp_path / "uv.log"),
        **variables,
    }
    return interpreter_store.acquire(
        source or archive(tmp_path), attempt, str(uv), tmp_path, environment
    )


def test_an_archive_is_installed_once_into_its_sealed_entry(store, tmp_path, uv):
    first = acquire(tmp_path, uv, attempt(tmp_path, "1"))
    assert first.root == store / SHA256
    assert first.executable == store / SHA256 / KEY / "bin" / "python3.14"
    assert first.executable.read_bytes() == PYTHON
    later = attempt(tmp_path, "2")
    assert acquire(tmp_path, uv, later) == first
    assert installs(tmp_path) == 1
    assert interpreter_store.linked(later, [first.executable])


def test_a_python_that_uv_finds_outside_the_entry_is_refused(store, tmp_path, uv):
    host = tmp_path.resolve() / "host python"
    host.write_text("")
    with pytest.raises(GatesealError) as refused:
        acquire(tmp_path, uv, attempt(tmp_path, "1"), FAKE_UV_FOUND=str(host))
    assert (refused.value.code, refused.value.exit_code) == ("invalid_preparation", 4)
    assert str(host) in str(refused.value)


def test_an_archive_that_cannot_be_fetched_names_network_access(store, tmp_path, uv):
    missing = archive(tmp_path, saved=False)
    with pytest.raises(GatesealError) as failed:
        acquire(tmp_path, uv, attempt(tmp_path, "1"), source=missing)
    assert archive_path(tmp_path).as_uri() in failed.value.next_step
    assert "gateseal activate" in failed.value.next_step
    assert not (store / (SHA256 + ".json")).exists()
