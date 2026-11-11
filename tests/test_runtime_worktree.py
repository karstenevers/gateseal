"""Where a worktree keeps its preparation, and what gateseal refuses to prepare."""

import shutil
import stat

import pytest
from gateseal import bootstrap, runtime
from gateseal.diagnostics import GatesealError
from installation import clone, success

REFUSED = "= \" ' $ ` \\ :"


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
    clone = runtime.locate_worktree(root)
    assert clone.preparation == root / ".git" / bootstrap.PREPARATION
    assert clone.hook == root / ".git" / "hooks" / "pre-commit"
    assert clone.display(root / "src" / "a.py") == "src/a.py"
    assert clone.display(tmp_path / "elsewhere") == str(tmp_path / "elsewhere")
    success(["git", "worktree", "add", "--quiet", tmp_path / "linked"], root)
    linked = runtime.locate_worktree(tmp_path / "linked")
    assert linked.git_dir == root / ".git" / "worktrees" / "linked"
    assert linked.preparation == linked.git_dir / bootstrap.PREPARATION
    assert linked.hook == clone.hook


def test_an_index_path_with_a_line_break_is_unsupported(root, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", f"{tmp_path}/index\n")
    error = refused(runtime.locate_worktree, root)
    assert error.code == "unsupported_path"


def test_another_index_is_refused_rather_than_ignored(root, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_INDEX_FILE", str(tmp_path / "other index"))
    error = refused(runtime.locate_worktree, root)
    assert error.code == "alternate_index"
    assert all(
        str(index) in str(error)
        for index in (tmp_path / "other index", root / ".git/index")
    )
    assert "GIT_INDEX_FILE" in error.next_step


def test_the_preparation_folder_is_private_and_made_only_on_request(root):
    worktree = runtime.locate_worktree(root)
    assert runtime.preparation_directory(worktree) == worktree.preparation
    assert not worktree.preparation.exists()
    for _ in range(2):
        found = runtime.preparation_directory(worktree, create=True)
        assert found == worktree.preparation
    assert stat.S_IMODE(found.stat().st_mode) == 0o700
    assert runtime.preparation_directory(worktree) == worktree.preparation


@pytest.mark.parametrize("kind", ["file", "link", "dangling link"])
def test_a_preparation_that_is_not_a_folder_is_refused(root, tmp_path, kind):
    worktree = runtime.locate_worktree(root)
    if kind == "file":
        worktree.preparation.write_text("")
    else:
        target = tmp_path / "target"
        if kind == "link":
            target.mkdir()
        worktree.preparation.symlink_to(target)
    error = refused(runtime.preparation_directory, worktree)
    assert error.code == "unsafe_path"


def test_one_gateseal_writer_runs_at_a_time(root):
    worktree = runtime.locate_worktree(root)
    local = runtime.preparation_directory(worktree, create=True)
    with runtime.operation_lock(local):
        with pytest.raises(GatesealError) as failure, runtime.operation_lock(local):
            pass
    error = failure.value
    assert (error.code, error.exit_code) == ("operation_in_progress", 4)
    with runtime.operation_lock(local):
        pass


@pytest.mark.parametrize("folder", ["a=b", "a:b"])
def test_a_clone_path_the_hook_cannot_pass_is_refused(root, tmp_path, folder):
    runtime.require_supported_path(runtime.locate_worktree(root))
    moved = tmp_path / folder
    root.rename(moved)
    worktree = runtime.locate_worktree(moved)
    error = refused(runtime.require_supported_path, worktree)
    assert error.code == "unsupported_path"
    assert str(worktree.git_dir) in str(error)
    assert REFUSED in error.next_step
    assert "gateseal activate" in error.next_step
    assert "git worktree remove" not in error.next_step


def test_a_linked_worktree_name_the_hook_cannot_pass_is_refused(root, tmp_path):
    success(["git", "worktree", "add", "--quiet", tmp_path / "wt$x"], root)
    worktree = runtime.locate_worktree(tmp_path / "wt$x")
    error = refused(runtime.require_supported_path, worktree)
    assert error.code == "unsupported_path"
    assert str(worktree.git_dir) in str(error)
    assert "git worktree remove" in error.next_step
    assert REFUSED in error.next_step


def test_the_gateseal_hook_is_owned_and_any_other_is_not(root):
    worktree = runtime.locate_worktree(root)
    worktree.hook.parent.mkdir(exist_ok=True)
    worktree.hook.write_bytes(bootstrap.HOOK)
    runtime.require_owned_hook(worktree)
    worktree.hook.write_bytes(bootstrap.HOOK + b"# edited\n")
    assert refused(runtime.require_owned_hook, worktree).code == "unmanaged_hook"


def test_an_unreadable_git_configuration_stops_the_hook_check(root):
    worktree = runtime.locate_worktree(root)
    with (root / ".git" / "config").open("a") as config:
        config.write("[core\n")
    error = refused(runtime.require_owned_hook, worktree)
    assert (error.code, error.exit_code) == ("git_configuration_failed", 4)
