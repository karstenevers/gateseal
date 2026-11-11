import os

import pytest
from gateseal import ci
from gateseal.diagnostics import GatesealError
from gateseal.runtime import locate_worktree
from installation import process, success

ZEROS = "0" * 40


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


def refused(plan, *revisions):
    with pytest.raises(GatesealError) as failure:
        plan(*revisions)
    return failure.value


def test_a_change_is_checked_from_its_merge_base(repository):
    base = commit(repository, "a")
    success(["git", "switch", "--quiet", "-c", "topic"], repository)
    head = commit(repository, "b")
    success(["git", "switch", "--quiet", "main"], repository)
    moved = commit(repository, "c")
    scope = ci.change_scope(locate_worktree(repository), moved, head)
    assert scope.files == (base, head)
    assert scope.history == f"{base}..{head}"
    assert base in scope.reason and moved in scope.reason


def test_unrelated_histories_are_checked_whole(repository):
    base = commit(repository, "a")
    success(["git", "switch", "--quiet", "--orphan", "other"], repository)
    head = commit(repository, "b")
    scope = ci.change_scope(locate_worktree(repository), base, head)
    assert (scope.files, scope.history) == (None, head)
    assert "share no history" in scope.reason


def test_a_fast_forward_push_is_checked_by_its_range(repository):
    before = commit(repository, "a")
    after = commit(repository, "b")
    scope = ci.push_scope(locate_worktree(repository), before, after)
    assert scope is not None
    assert scope.files == (before, after)
    assert scope.history == f"{before}..{after}"


@pytest.mark.parametrize(
    "case,reason",
    [
        ("rewrite", "rewrote history"),
        ("new branch", "created its branch"),
        ("missing", "The clone lacks"),
    ],
)
def test_a_push_without_its_own_range_is_checked_whole(repository, case, reason):
    first = commit(repository, "a")
    replaced = commit(repository, "b")
    success(["git", "reset", "--quiet", "--hard", first], repository)
    after = commit(repository, "c")
    before = {
        "rewrite": replaced,
        "new branch": ZEROS,
        "missing": "1" * 40,
    }[case]
    scope = ci.push_scope(locate_worktree(repository), before, after)
    assert scope is not None
    assert (scope.files, scope.history) == (None, after)
    assert reason in scope.reason


def test_a_deleted_ref_leaves_nothing_to_check(repository):
    before = commit(repository, "a")
    assert ci.push_scope(locate_worktree(repository), before, ZEROS) is None


def test_every_file_scans_no_history():
    scope = ci.full_scope()
    assert (scope.files, scope.history) == (None, None)


@pytest.mark.parametrize(
    "revision", ["HEAD", "--output=x", "A" * 40, "a" * 39, "a" * 41, ""]
)
def test_only_full_object_ids_are_revisions(repository, revision):
    head = commit(repository, "a")
    worktree = locate_worktree(repository)
    for plan, revisions in (
        (ci.change_scope, (revision, head)),
        (ci.change_scope, (head, revision)),
        (ci.push_scope, (revision, head)),
        (ci.push_scope, (head, revision)),
    ):
        assert refused(plan, worktree, *revisions).code == "invalid_invocation"


def test_a_revision_the_clone_lacks_names_full_history(repository):
    head = commit(repository, "a")
    worktree = locate_worktree(repository)
    missing = "1" * 40
    for failure in (
        refused(ci.change_scope, worktree, missing, head),
        refused(ci.change_scope, worktree, head, missing),
        refused(ci.push_scope, worktree, head, missing),
    ):
        assert failure.code == "unknown_revision"
        assert failure.exit_code == 2
        assert "fetch-depth: 0" in failure.next_step


def test_a_tree_is_not_a_commit(repository):
    commit(repository, "a")
    tree = success(["git", "rev-parse", "HEAD^{tree}"], repository).stdout.strip()
    head = success(["git", "rev-parse", "HEAD"], repository).stdout.strip()
    failure = refused(ci.change_scope, locate_worktree(repository), tree, head)
    assert failure.code == "unknown_revision"


def test_a_partial_clone_fetches_no_missing_commit(repository, tmp_path):
    base = commit(repository, "a")
    for key in ("uploadpack.allowFilter", "uploadpack.allowAnySHA1InWant"):
        success(["git", "config", key, "true"], repository)
    clone = tmp_path / "partial"
    success(
        ["git", "clone", "--quiet", "--filter=blob:none", repository.as_uri(), clone],
        tmp_path,
    )
    success(["git", "switch", "--quiet", "-c", "unfetched"], repository)
    later = commit(repository, "b")
    failure = refused(ci.change_scope, locate_worktree(clone), base, later)
    assert failure.code == "unknown_revision"
    lacks = ["git", "-c", "fetch.negotiationAlgorithm=noop", "cat-file", "-e", later]
    assert process(lacks, clone, {**os.environ, "GIT_NO_LAZY_FETCH": "1"}).returncode
