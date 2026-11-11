"""A sealed entry is read-only throughout, whatever modes its build left.

A build may leave a folder that its own owner cannot list, such as mode 0o300.
Sealing still gives every file in it a read-only mode, so no other worktree's
commit can write through the shared entry.
"""

import stat

import pytest
from gateseal import interpreter_store
from test_interpreter_store_collect import owner_permissions


@pytest.fixture
def store(monkeypatch, tmp_path):
    store = tmp_path.resolve() / "store"
    monkeypatch.setenv(interpreter_store.STORE_VARIABLE, str(store))
    return store


def closed_build(entry, lock):
    """Leave the entry and a folder in it unreadable to their owner."""
    closed = entry / "lib" / "closed"
    closed.mkdir(parents=True)
    (closed / "module.py").write_text("")
    (closed / "tool").write_text("#!/bin/sh\n")
    (closed / "tool").chmod(0o750)
    closed.chmod(0o300)
    entry.chmod(0o300)


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


def test_an_entry_seals_the_files_of_folders_its_build_closed(store, tmp_path):
    attempt = tmp_path / ("attempt-" + "a" * 32)
    attempt.mkdir()
    with owner_permissions():
        with interpreter_store.sealed(
            "f" * 64, {"key": "closed"}, attempt, closed_build
        ) as (entry, _):
            pass
    closed = entry / "lib" / "closed"
    assert (mode(entry), mode(closed)) == (0o555, 0o555)
    assert (mode(closed / "module.py"), mode(closed / "tool")) == (0o444, 0o555)
