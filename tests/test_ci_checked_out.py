"""The gate checks a named head only when the worktree holds it unchanged."""

import pytest
from gateseal import ci
from gateseal.diagnostics import GatesealError
from gateseal.runtime import locate_worktree
from installation import success

RETRY = "gateseal gate"


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], tmp_path)
    for key, value in (("user.name", "gateseal test"), ("user.email", "t@x.invalid")):
        success(["git", "config", key, value], root)
    return root


def commit(root, name):
    (root / name).write_text(name + "\n")
    success(["git", "add", name], root)
    success(["git", "commit", "--quiet", "-m", name], root)
    return success(["git", "rev-parse", "HEAD"], root).stdout.strip()


def refused(root, head):
    with pytest.raises(GatesealError) as failure:
        ci.require_checked_out(locate_worktree(root), head)
    return failure.value


def test_the_committed_head_passes(repository):
    head = commit(repository, "a")
    ci.require_checked_out(locate_worktree(repository), head)


@pytest.mark.parametrize("checked_out", ["another commit", "no commit yet"])
def test_a_worktree_without_the_head_is_refused(repository, checked_out):
    if checked_out == "another commit":
        head = commit(repository, "a")
        commit(repository, "b")
    else:
        head = "1" * 40
    error = refused(repository, head)
    assert (error.code, error.exit_code) == ("head_not_checked_out", 2)
    assert head in str(error)
    assert all(value in error.next_step for value in (head, RETRY))


@pytest.mark.parametrize("staged", [False, True])
def test_an_edit_of_the_head_is_refused(repository, staged):
    head = commit(repository, "a")
    (repository / "a").write_text("edited\n")
    if staged:
        success(["git", "add", "a"], repository)
    error = refused(repository, head)
    assert error.code == "uncommitted_changes"
    assert head in str(error)
    assert RETRY in error.next_step


def test_a_git_failure_names_the_command_and_its_message(repository):
    head = commit(repository, "a")
    (repository / ".git" / "index").write_bytes(b"not an index")
    error = refused(repository, head)
    assert (error.code, error.exit_code) == ("tool_failed", 4)
    assert all(value in str(error) for value in ("git diff", "index"))
