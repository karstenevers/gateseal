"""gateseal reads and writes the files it owns without following a link.

Each test calls managed_files on real files in tmp_path under a known umask,
so the modes a call leaves are the ones it chose.
"""

import os
import stat
import threading

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import (
    make_parents,
    read,
    read_optional,
    relative_name,
    remove,
    write_atomic,
)


@pytest.fixture(autouse=True)
def umask():
    previous = os.umask(0o022)
    yield
    os.umask(previous)


def failure(call, *arguments, **keywords):
    with pytest.raises(GatesealError) as raised:
        call(*arguments, **keywords)
    return raised.value


def mode(path):
    return stat.S_IMODE(path.lstat().st_mode)


@pytest.mark.parametrize("name", ["", "rules\\ruff.toml", "c:ruff.toml", "a\x00b"])
def test_a_name_with_a_separator_or_drive_is_not_relative(name):
    error = failure(relative_name, name)
    assert error.code == "unsafe_path"
    assert "contained" in str(error)


@pytest.mark.parametrize("name", ["../ruff.toml", "/etc/passwd", "a//b", "./a"])
def test_a_name_that_leaves_or_repeats_its_root_is_unsafe(name):
    error = failure(relative_name, name)
    assert error.code == "unsafe_path"
    assert repr(name) in str(error)


def test_a_contained_name_is_returned():
    assert relative_name("rules/ruff.toml") == "rules/ruff.toml"


def test_read_never_leaves_its_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (tmp_path / "secret").write_bytes(b"token")
    assert failure(read, root, "../secret").code == "unsafe_path"


def test_read_refuses_a_symlinked_file(tmp_path):
    (tmp_path / "target").write_bytes(b"data")
    (tmp_path / "link").symlink_to(tmp_path / "target")
    error = failure(read, tmp_path, "link")
    assert error.code == "unsafe_path"
    assert "link" in str(error)


def test_read_refuses_a_file_where_a_directory_belongs(tmp_path):
    (tmp_path / "rules").write_bytes(b"not a directory")
    error = failure(read, tmp_path, "rules/ruff.toml")
    assert error.code == "unsafe_path"
    assert str(tmp_path / "rules") in str(error)


def test_a_missing_file_reads_as_none(tmp_path):
    assert read_optional(tmp_path, "missing.toml") is None
    (tmp_path / "found.toml").write_bytes(b"x = 1\n")
    assert read_optional(tmp_path, "found.toml") == b"x = 1\n"


@pytest.mark.parametrize(("bits", "shown"), [(0o645, "0645"), (0o654, "0654")])
def test_a_non_executable_read_refuses_any_execute_bit(tmp_path, bits, shown):
    path = tmp_path / "policy.toml"
    path.write_bytes(b"schema = 4\n")
    path.chmod(bits)
    error = failure(read, tmp_path, "policy.toml", non_executable=True)
    assert error.code == "file_mode_mismatch"
    assert all(value in str(error) for value in ("policy.toml", shown))
    assert read(tmp_path, "policy.toml") == b"schema = 4\n"


def test_a_writable_file_without_execute_bits_reads(tmp_path):
    path = tmp_path / "policy.toml"
    path.write_bytes(b"schema = 4\n")
    path.chmod(0o666)
    assert read(tmp_path, "policy.toml", non_executable=True) == b"schema = 4\n"


def test_a_file_that_changes_while_it_is_read_is_refused(tmp_path):
    """Another process rewrites the file's times while gateseal reads it.

    A read of a large file releases the GIL, so the writer thread runs during
    it; enough attempts make sure one read overlaps a change.
    """
    path = tmp_path / "policy.lock"
    path.write_bytes(b"x" * (8 << 20))
    stop = threading.Event()

    def touch():
        tick = 0
        while not stop.is_set():
            tick += 1
            os.utime(path, ns=(tick, tick))

    writer = threading.Thread(target=touch)
    writer.start()
    errors = []
    try:
        for _ in range(200):
            try:
                read(tmp_path, "policy.lock")
            except GatesealError as error:
                errors.append(error)
                break
    finally:
        stop.set()
        writer.join()
    assert [error.code for error in errors] == ["concurrent_change"]
    assert "policy.lock" in str(errors[0])


def test_make_parents_creates_each_missing_directory(tmp_path):
    (tmp_path / ".gateseal").mkdir(mode=0o700)
    make_parents(tmp_path, ".gateseal/rules/team/ruff.toml")
    assert mode(tmp_path / ".gateseal") == 0o700
    assert mode(tmp_path / ".gateseal" / "rules") == 0o755
    assert mode(tmp_path / ".gateseal" / "rules" / "team") == 0o755
    assert not (tmp_path / ".gateseal" / "rules" / "team" / "ruff.toml").exists()
    make_parents(tmp_path, ".gateseal/rules/team/ruff.toml")


def test_make_parents_never_follows_a_symlinked_directory(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    assert failure(make_parents, tmp_path, "link/rules/ruff.toml").code == "unsafe_path"
    assert list(outside.iterdir()) == []


def test_remove_deletes_the_file_and_accepts_a_missing_one(tmp_path):
    (tmp_path / "rules").mkdir()
    (tmp_path / "rules" / "ruff.toml").write_bytes(b"x")
    (tmp_path / "rules" / "keep.toml").write_bytes(b"y")
    remove(tmp_path, "rules/ruff.toml")
    assert sorted(p.name for p in (tmp_path / "rules").iterdir()) == ["keep.toml"]
    remove(tmp_path, "rules/ruff.toml")
    assert failure(remove, tmp_path, "../rules/keep.toml").code == "unsafe_path"


@pytest.mark.parametrize(("chosen", "expected"), [((), 0o600), ((0o664,), 0o664)])
def test_write_atomic_replaces_the_file_with_the_chosen_mode(
    tmp_path, chosen, expected
):
    path = tmp_path / "policy.lock"
    path.write_bytes(b"old")
    path.chmod(0o640)
    write_atomic(path, b"new", *chosen)
    assert path.read_bytes() == b"new"
    assert mode(path) == expected
    assert [p.name for p in tmp_path.iterdir()] == ["policy.lock"]


def test_write_atomic_refuses_a_file_where_its_directory_belongs(tmp_path):
    (tmp_path / "rules").write_bytes(b"not a directory")
    assert failure(write_atomic, tmp_path / "rules" / "x", b"x").code == "unsafe_path"


def test_a_failed_write_leaves_no_temporary_file(tmp_path):
    (tmp_path / "policy.lock").mkdir()
    with pytest.raises(IsADirectoryError):
        write_atomic(tmp_path / "policy.lock", b"new")
    assert [p.name for p in tmp_path.iterdir()] == ["policy.lock"]
