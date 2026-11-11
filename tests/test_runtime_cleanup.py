"""Attempt cleanup and the unstaged-edit patches pre-commit leaves in attempts.

Each test builds the preparation directory a commit and an activation leave,
with patches written as pre-commit writes them, and runs cleanup against a
real clone.
"""

import fcntl
import os
import shutil
import stat
import time
import uuid

import pytest
from gateseal import bootstrap, runtime
from installation import clone, process, success

NOW = 2_000_000_000
"""The frozen clock's time, a whole second, so an age lands exactly on it."""

ORIGINAL = "one\ntwo\n"


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    directory = tmp_path_factory.mktemp("template")
    root = clone(directory / "origin", directory / "consumer")
    (root / "notes.txt").write_text(ORIGINAL)
    (root / "other.txt").write_text(ORIGINAL)
    success(["git", "add", "notes.txt", "other.txt"], root)
    success(["git", "commit", "--quiet", "-m", "Notes"], root)
    return root


@pytest.fixture
def worktree(template, tmp_path):
    root = tmp_path / "consumer"
    shutil.copytree(template, root, symlinks=True)
    found = runtime.locate_worktree(root)
    runtime.preparation_directory(found, create=True)
    return found


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: float(NOW))


def unstaged(root):
    """Return the patch pre-commit saves of root's unstaged edits."""
    tree = success(["git", "write-tree"], root).stdout.strip()
    found = process(
        ["git", "diff-index", "--ignore-submodules", "--binary", "--exit-code"]
        + ["--no-color", "--no-ext-diff", tree, "--"],
        root,
    )
    assert found.returncode == 1, found.stderr
    return found.stdout.encode()


def lose(root):
    """Clear the worktree's edits, as pre-commit does before its hooks run."""
    success(["git", "checkout", "--", "."], root)


def attempt(local, patches=None):
    """Create an attempt whose pre-commit home holds patches, by name: (data, age)."""
    path = local / ("attempt-" + uuid.uuid4().hex)
    home = path / "pre-commit-home"
    home.mkdir(parents=True)
    for name, (data, age) in (patches or {}).items():
        (home / name).write_bytes(data)
        moment = (NOW - age) * 1_000_000_000
        os.utime(home / name, ns=(moment, moment))
    return path


def clean(worktree):
    runtime._clean_up(worktree.preparation, worktree.root, worktree.git)


def kept(local):
    folder = local / runtime.PATCHES
    return {p.name: p.read_bytes() for p in folder.iterdir()} if folder.exists() else {}


def test_cleanup_deletes_every_attempt_but_the_current_one(worktree, tmp_path):
    local = worktree.preparation
    current = attempt(local)
    (local / bootstrap.CURRENT).symlink_to(current.name)
    attempt(local)
    (local / ("attempt-" + "f" * 32)).write_text("")
    (local / ".gateseal-0123").symlink_to("nowhere")
    outside = tmp_path / "outside"
    (outside / "pre-commit-home").mkdir(parents=True)
    (outside / "pre-commit-home" / "data").write_text("kept")
    (local / ("attempt-" + "e" * 32)).symlink_to(outside)
    (local / "attempt-unrelated").mkdir()
    (local / runtime.PATCHES).mkdir()
    (local / "operation.lock").write_text("")
    clean(worktree)
    assert {path.name for path in local.iterdir()} == {
        bootstrap.CURRENT,
        current.name,
        bootstrap.ATTEMPTS_LOCK,
        "attempt-unrelated",
        runtime.PATCHES,
        "operation.lock",
    }
    assert (outside / "pre-commit-home" / "data").read_text() == "kept"


def test_a_current_folder_a_copy_left_is_deleted(worktree):
    local = worktree.preparation
    (local / bootstrap.CURRENT).mkdir()
    stale = attempt(local)
    clean(worktree)
    assert not (local / bootstrap.CURRENT).exists()
    assert not stale.exists()


def test_cleanup_deletes_nothing_while_a_commit_runs(worktree):
    local = worktree.preparation
    stale = attempt(local)
    descriptor = bootstrap.open_lock(local / bootstrap.ATTEMPTS_LOCK)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        clean(worktree)
        assert stale.is_dir()
    finally:
        os.close(descriptor)
    clean(worktree)
    assert not stale.exists()


def test_a_patch_whose_edits_the_worktree_lost_is_kept(worktree, clock):
    root, local = worktree.root, worktree.preparation
    (root / "notes.txt").write_text("one\nthree\n")
    patch = unstaged(root)
    lose(root)
    stale = attempt(local, {"patch1-1": (patch, 60)})
    clean(worktree)
    assert not stale.exists()
    assert kept(local) == {f"{stale.name}-patch1-1": patch}
    assert stat.S_IMODE((local / runtime.PATCHES).stat().st_mode) == 0o700


@pytest.mark.parametrize("age,waits", [(3600, False), (3599, True)])
def test_a_restored_patch_waits_an_hour_after_pre_commit_wrote_it(
    worktree, clock, age, waits
):
    root, local = worktree.root, worktree.preparation
    (root / "notes.txt").write_text("one\nthree\n")
    stale = attempt(local, {"patch1-1": (unstaged(root), age)})
    clean(worktree)
    assert stale.is_dir() is waits
    assert (stale / "pre-commit-home" / "patch1-1").exists() is waits
    assert not (local / runtime.PATCHES).exists()


def test_a_waiting_patch_keeps_its_attempt_once_a_lost_one_moves(worktree, clock):
    root, local = worktree.root, worktree.preparation
    (root / "other.txt").write_text("other edit\n")
    lost = unstaged(root)
    lose(root)
    (root / "notes.txt").write_text("one\nthree\n")
    restored = unstaged(root)
    stale = attempt(local, {"patch1-1": (lost, 60), "patch2-2": (restored, 60)})
    clean(worktree)
    assert kept(local) == {f"{stale.name}-patch1-1": lost}
    assert os.listdir(stale / "pre-commit-home") == ["patch2-2"]


def test_an_old_restored_patch_goes_while_a_new_one_waits(worktree, clock):
    root, local = worktree.root, worktree.preparation
    (root / "notes.txt").write_text("one\nthree\n")
    patch = unstaged(root)
    stale = attempt(local, {"patch1-1": (patch, 7200), "patch2-2": (patch, 60)})
    clean(worktree)
    assert os.listdir(stale / "pre-commit-home") == ["patch2-2"]
    assert kept(local) == {}


@pytest.mark.parametrize("home", ["missing", "file", "link"])
def test_an_attempt_without_a_pre_commit_folder_is_deleted(
    worktree, tmp_path, clock, home
):
    root, local = worktree.root, worktree.preparation
    stale = attempt(local)
    (stale / "pre-commit-home").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "notes.txt").write_text("one\nthree\n")
    (outside / "patch1-1").write_bytes(unstaged(root))
    lose(root)
    if home == "file":
        (stale / "pre-commit-home").write_text("")
    elif home == "link":
        (stale / "pre-commit-home").symlink_to(outside)
    clean(worktree)
    assert not stale.exists()
    assert os.listdir(outside) == ["patch1-1"]
    assert kept(local) == {}


def test_only_regular_patch_files_count_as_patches(worktree, tmp_path, clock):
    local = worktree.preparation
    stale = attempt(local)
    home = stale / "pre-commit-home"
    (home / "db.db").write_text("not a patch")
    (home / "patchdir").mkdir()
    (tmp_path / "elsewhere").write_text("not a patch")
    (home / "patch-link").symlink_to(tmp_path / "elsewhere")
    clean(worktree)
    assert not stale.exists()
    assert kept(local) == {}
    assert (tmp_path / "elsewhere").read_text() == "not a patch"


@pytest.mark.parametrize("kind", ["link", "fifo"])
def test_a_lost_patch_stays_in_its_attempt_when_patches_is_not_a_folder(
    worktree, tmp_path, clock, kind
):
    root, local = worktree.root, worktree.preparation
    (root / "notes.txt").write_text("one\nthree\n")
    patch = unstaged(root)
    lose(root)
    stale = attempt(local, {"patch1-1": (patch, 60)})
    (tmp_path / "elsewhere").mkdir()
    if kind == "link":
        (local / runtime.PATCHES).symlink_to(tmp_path / "elsewhere")
    else:
        os.mkfifo(local / runtime.PATCHES)
    clean(worktree)
    assert (stale / "pre-commit-home" / "patch1-1").read_bytes() == patch
    assert os.listdir(tmp_path / "elsewhere") == []


def edit(root):
    (root / "notes.txt").write_text("one\nthree\n")


def delete(root):
    (root / "notes.txt").unlink()


def chmod(root):
    (root / "notes.txt").chmod(0o755)


@pytest.mark.parametrize("change", [edit, delete, chmod])
@pytest.mark.parametrize("lost", [False, True])
def test_a_patch_is_kept_unless_the_worktree_holds_its_result(
    worktree, clock, change, lost
):
    root, local = worktree.root, worktree.preparation
    change(root)
    patch = unstaged(root)
    if lost:
        lose(root)
    stale = attempt(local, {"patch1-1": (patch, 7200)})
    clean(worktree)
    assert not stale.exists()
    assert kept(local) == ({f"{stale.name}-patch1-1": patch} if lost else {})


@pytest.mark.parametrize("corrupt", ["hunk", "text"])
def test_a_patch_that_cannot_apply_to_its_own_pre_image_is_kept(
    worktree, clock, corrupt
):
    root, local = worktree.root, worktree.preparation
    edit(root)
    patch = unstaged(root)
    patch = patch.replace(b"\n one\n", b"\n uno\n") if corrupt == "hunk" else b"x\n"
    stale = attempt(local, {"patch1-1": (patch, 7200)})
    clean(worktree)
    assert kept(local) == {f"{stale.name}-patch1-1": patch}
