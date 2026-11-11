"""Start a policy directory, and write policy files only as they were read."""

import fcntl
import os
import stat
import threading
import time

import pytest
from gateseal import authoring
from gateseal.diagnostics import GatesealError
from gateseal.policy import bundled_root, load_bundled_policy

BUNDLED = (
    "policy.toml",
    "policy.lock",
    "rules/betterleaks.toml",
    "rules/ruff.toml",
    "README.md",
)


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


@pytest.fixture
def started(tmp_path):
    root = tmp_path / "policy"
    authoring.init(root)
    return root


def test_init_writes_the_bundled_policy_into_a_new_directory(tmp_path):
    root = tmp_path / "team ü" / "policy"
    result = authoring.init(root)
    assert (result.code, sorted(result.changed)) == ("initialized", sorted(BUNDLED))
    for name in BUNDLED:
        assert (root / name).read_bytes() == (bundled_root() / name).read_bytes()
        assert mode(root / name) == 0o644
    policy = load_bundled_policy()
    [note] = result.notes
    assert (note.severity, note.category) == ("info", "policy")
    assert policy.release in note.message and policy.content_digest in note.message
    [step] = result.next_steps
    named = ("README.md", "policy.toml", "gateseal policy lock")
    assert all(value in step for value in named)
    assert authoring.check(root).code == "consistent"


def test_init_fills_an_empty_directory_but_never_one_with_files(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert authoring.init(empty).code == "initialized"
    kept = tmp_path / "used" / "notes.txt"
    kept.parent.mkdir()
    kept.write_text("ours\n")
    error = refused(authoring.init, kept.parent)
    assert error.code == "directory_not_empty"
    assert list(kept.parent.iterdir()) == [kept]


def test_an_executable_policy_file_is_refused(started):
    (started / "policy.toml").chmod(0o755)
    assert refused(authoring.check, started).code == "file_mode_mismatch"


def test_a_file_made_executable_before_the_write_stops_it(started):
    snapshot = authoring.Snapshot(started)
    before = snapshot.read("policy.toml")
    (started / "policy.toml").chmod(0o755)
    error = refused(snapshot.write, {"policy.toml": before + b"# Edited.\n"})
    assert error.code == "file_mode_mismatch"
    assert (started / "policy.toml").read_bytes() == before


def test_a_write_keeps_each_files_permissions(started):
    (started / "policy.toml").chmod(0o640)
    snapshot = authoring.Snapshot(started)
    edited = snapshot.read("policy.toml") + b"# Edited.\n"
    assert snapshot.write({"policy.toml": edited, "NOTES": b"new\n"}) == (
        "policy.toml",
        "NOTES",
    )
    assert (started / "policy.toml").read_bytes() == edited
    assert mode(started / "policy.toml") == 0o640
    assert mode(started / "NOTES") == 0o644


def test_a_write_waits_for_another_policy_command(started):
    snapshot = authoring.Snapshot(started)
    edited = snapshot.read("policy.toml") + b"# Edited.\n"
    directory = os.open(started, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        writer = threading.Thread(
            target=snapshot.write, args=({"policy.toml": edited},)
        )
        writer.start()
        time.sleep(0.1)
        assert (started / "policy.toml").read_bytes() != edited
    finally:
        os.close(directory)
    writer.join(5)
    assert (started / "policy.toml").read_bytes() == edited
