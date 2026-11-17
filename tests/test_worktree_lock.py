"""Where a worktree keeps its folder in the Git directory, and its writer lock."""

import shutil
import stat

import pytest
from gateseal import sources
from gateseal.diagnostics import GatesealError
from gateseal.worktree import (
    PREPARATION,
    locate_worktree,
    operation_lock,
    preparation_directory,
)
from installation import clone, success


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    directory = tmp_path_factory.mktemp("template")
    found = clone(directory / "origin", directory / "consumer")
    (found / "notes.txt").write_text("notes\n")
    success(["git", "add", "notes.txt"], found)
    success(["git", "commit", "--quiet", "-m", "Notes"], found)
    return found


@pytest.fixture
def root(template, tmp_path):
    shutil.copytree(template, tmp_path / "consumer", symlinks=True)
    return tmp_path / "consumer"


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


def test_a_clone_and_its_linked_worktree_prepare_in_their_own_git_directory(
    root, tmp_path
):
    clone = locate_worktree(root)
    assert clone.preparation == root / ".git" / PREPARATION
    assert clone.hook == root / ".git" / "hooks" / "pre-commit"
    success(["git", "worktree", "add", "--quiet", tmp_path / "linked"], root)
    linked = locate_worktree(tmp_path / "linked")
    assert linked.git_dir == root / ".git" / "worktrees" / "linked"
    assert linked.preparation == linked.git_dir / PREPARATION
    assert linked.hook == clone.hook


def test_an_index_path_with_a_line_break_is_unsupported(root, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", f"{tmp_path}/index\n")
    error = refused(locate_worktree, root)
    assert error.code == "unsupported_path"


def test_another_index_is_refused_rather_than_ignored(root, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "other index"))
    error = refused(locate_worktree, root)
    assert error.code == "alternate_index"
    assert all(
        str(index) in str(error)
        for index in (tmp_path / "other index", root / ".git/index")
    )
    assert "GIT_INDEX_FILE" in error.next_step


def test_the_preparation_folder_is_private_and_made_once(root):
    worktree = locate_worktree(root)
    assert not worktree.preparation.exists()
    for _ in range(2):
        found = preparation_directory(worktree)
        assert found == worktree.preparation
    assert stat.S_IMODE(found.stat().st_mode) == 0o700


@pytest.mark.parametrize("kind", ["file", "link", "dangling link"])
def test_a_preparation_that_is_not_a_folder_is_refused(root, tmp_path, kind):
    worktree = locate_worktree(root)
    if kind == "file":
        worktree.preparation.write_text("")
    else:
        target = tmp_path / "target"
        if kind == "link":
            target.mkdir()
        worktree.preparation.symlink_to(target)
    error = refused(preparation_directory, worktree)
    assert error.code == "unsafe_path"


def test_one_gateseal_writer_runs_at_a_time(root):
    worktree = locate_worktree(root)
    local = preparation_directory(worktree)
    scratch = local / ".gateseal-0a"
    with operation_lock(local):
        scratch.mkdir()
        with pytest.raises(GatesealError) as failure, operation_lock(local):
            pass
        assert scratch.is_dir()
    error = failure.value
    assert (error.code, error.exit_code) == ("operation_in_progress", 4)
    with operation_lock(local):
        pass


def test_a_writer_deletes_the_scratch_folders_a_killed_one_left(root, tmp_path):
    worktree = locate_worktree(root)
    local = preparation_directory(worktree)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "kept").write_text("kept\n")
    (local / ".gateseal-0a.git" / "objects").mkdir(parents=True)
    (local / ".gateseal-0b" / "repository").mkdir(parents=True)
    (local / ".gateseal-link").symlink_to(outside, target_is_directory=True)
    (local / sources.STORE / ".gateseal-0c").mkdir(parents=True)
    with operation_lock(local):
        left = sorted(entry.name for entry in local.iterdir())
    assert left == sorted([".gateseal-link", "operation.lock", sources.STORE])
    assert (local / sources.STORE / ".gateseal-0c").is_dir()
    assert (outside / "kept").read_text() == "kept\n"


def test_the_writer_lock_is_a_private_file_and_never_a_link(root, tmp_path):
    local = preparation_directory(locate_worktree(root))
    lock = local / "operation.lock"
    with operation_lock(local):
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    lock.unlink()
    target = tmp_path / "target"
    target.write_text("")
    lock.symlink_to(target)
    with pytest.raises(OSError), operation_lock(local):
        pass
