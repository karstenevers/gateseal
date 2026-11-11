"""The commit-time hook entry, run in this process up to its exec.

The hook runs main inside the prepared runtime, which coverage and pytestmut
cannot see. os.execve, the boundary to the prepared compiler, is replaced by a
stand-in that does what exec does to the attempts lock, closing each
close-on-exec descriptor, and then stops main.
"""

import fcntl
import os
import sys

import pytest
from conftest import overwrite, stderr_refusal
from gateseal import bootstrap
from test_bootstrap_staged import CONFIG, MISE, blobs, staged_repository


class Executed(Exception):
    def __init__(self, path, argv, env, cleanup_blocked):
        super().__init__(path)
        self.path, self.argv, self.env = path, argv, env
        self.cleanup_blocked = cleanup_blocked


def lock_descriptors(lock):
    target = lock.stat()
    found = []
    for name in os.listdir("/dev/fd"):
        try:
            if os.path.samestat(os.fstat(int(name)), target):
                found.append(int(name))
        except OSError:
            continue
    return found


def cleanup_blocked(lock):
    """Try the exclusive lock that activation's cleanup takes before it deletes."""
    probe = os.open(lock, os.O_RDWR)
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(probe)
    return False


@pytest.fixture
def record(tmp_path, monkeypatch):
    """A staged repository whose hook runs main from its root."""
    record = staged_repository(tmp_path)
    lock = record.attempt.parent / bootstrap.ATTEMPTS_LOCK

    def execve(path, argv, env):
        for descriptor in lock_descriptors(lock):
            if not os.get_inheritable(descriptor):
                os.close(descriptor)
        raise Executed(path, argv, env, cleanup_blocked(lock))

    monkeypatch.setattr(os, "execve", execve)
    monkeypatch.chdir(record.root)
    yield record
    if lock.exists():
        for descriptor in lock_descriptors(lock):
            os.close(descriptor)


def run_hook(capsys, arguments):
    """Run main to its end and return its exit code, message and next step."""
    code = bootstrap.main(arguments)
    return code, *stderr_refusal(capsys.readouterr().err)


def test_a_passing_commit_becomes_the_compilers_staged_holding_the_lock(
    record, monkeypatch
):
    monkeypatch.setenv("VIRTUAL_ENV", "/caller venv")
    with pytest.raises(Executed) as executed:
        bootstrap.main([str(record.attempt)])
    python = str(record.paths.compiler_python)
    oids = blobs(record.root, "gateseal.toml", "gateseal.lock", MISE, CONFIG)
    assert executed.value.path == python
    assert executed.value.argv == [
        *(python, "-I", "-B", "-m", "gateseal", "_staged", str(record.attempt)),
        *oids,
    ]
    assert executed.value.cleanup_blocked, "the commit released the attempts lock"
    assert executed.value.env["UV_OFFLINE"] == "1"
    assert "VIRTUAL_ENV" not in executed.value.env


def test_the_hook_names_the_attempt_as_its_only_argument(record, monkeypatch):
    monkeypatch.setattr(sys, "argv", [str(record.paths.bootstrap), str(record.attempt)])
    with pytest.raises(Executed):
        bootstrap.main()


@pytest.mark.parametrize("count", [0, 2])
def test_the_hook_passes_exactly_one_attempt(record, capsys, count):
    code, _, step = run_hook(capsys, [str(record.attempt)] * count)
    assert (code, step) == (3, bootstrap.ACTIVATE)


def test_the_hook_passes_an_absolute_attempt(record, capsys):
    relative = os.path.relpath(record.attempt)
    code, _, step = run_hook(capsys, [relative])
    assert (code, step) == (3, bootstrap.ACTIVATE)


def test_a_commit_from_a_moved_clone_is_sent_to_activate_there(
    record, tmp_path, capsys, monkeypatch
):
    moved = tmp_path / "moved clone"
    moved.mkdir()
    monkeypatch.chdir(moved)
    code, message, step = run_hook(capsys, [str(record.attempt)])
    assert code == 3
    assert str(record.root) in message
    assert f"gateseal activate in {moved.resolve()}" in step


def test_a_commit_stops_on_a_changed_tool_directory(record, capsys):
    (record.paths.tools / "git").symlink_to(record.paths.git)
    code, message, step = run_hook(capsys, [str(record.attempt)])
    assert (code, step) == (3, bootstrap.ACTIVATE)
    assert str(record.paths.tools) in message


def test_a_commit_does_not_hash_the_runtime_content(record):
    overwrite(record.paths.compiler_python, b"#!/bin/ok\n")
    with pytest.raises(Executed):
        bootstrap.main([str(record.attempt)])


def test_a_missing_preparation_directory_names_activate(record, capsys):
    attempt = record.attempt.parent / "removed" / record.attempt.name
    code, message, step = run_hook(capsys, [str(attempt)])
    assert (code, step) == (3, bootstrap.ACTIVATE)
    assert str(attempt.parent) in message
