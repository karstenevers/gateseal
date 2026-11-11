"""What the CI gate checks for one event: which files, and which commits.

Planning reads only the clone. It never fetches, since CI checks out the full
history without persisted credentials.
"""

import re
import subprocess
from dataclasses import dataclass

from gateseal.bootstrap import clean_environment
from gateseal.diagnostics import GatesealError
from gateseal.processes import execute
from gateseal.runtime import Worktree

OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
"""A full object ID, which no Git command can read as an option."""

DELETED = re.compile(r"0{40}|0{64}")
"""The object ID an event names for a ref that did not or no longer exists."""

OFFLINE = {"GIT_NO_LAZY_FETCH": "1"}
"""Keep a partial clone from fetching a missing object while the gate runs."""


@dataclass(frozen=True)
class Scope:
    """What one gate checks.

    files is the pair that pre-commit's --from-ref and --to-ref take, or None
    for every file. history is the revision range the history scans read, or
    None for no scan. head is the commit the event names, which pre-commit
    reads from the worktree, so it must be checked out without changes; None
    checks the worktree as it is. reason is the sentence the result reports.
    """

    files: tuple[str, str] | None
    history: str | None
    head: str | None
    reason: str


def full_scope() -> Scope:
    """Check every file and scan no history, as a scheduled or manual run does."""
    return Scope(None, None, None, "The gate checks every file and scans no history.")


def change_scope(worktree: Worktree, base: str, head: str) -> Scope:
    """Check what head changes since its merge base with base.

    A pull request or merge group names both. When they share no history,
    every file and every commit reachable from head are checked.
    """
    _require(worktree, _object_id(base, "--base"))
    _require(worktree, _object_id(head, "--head"))
    found = _git(worktree, "merge-base", base, head)
    if found.returncode == 1:
        return Scope(
            None,
            head,
            head,
            f"{base} and {head} share no history, so the gate checks every file "
            f"and scans every commit reachable from {head}.",
        )
    merge_base = found.stdout.decode().strip()
    return Scope(
        (merge_base, head),
        f"{merge_base}..{head}",
        head,
        f"The gate checks the files and commits that {head} changes since its "
        f"merge base {merge_base} with {base}.",
    )


def push_scope(worktree: Worktree, before: str, after: str) -> Scope | None:
    """Check what a push added, or return None when it deleted its ref.

    A new branch, a before the clone lacks, and a rewrite that drops before
    from after's history all leave no range the push alone added, so every
    file and every commit reachable from after are checked.
    """
    _object_id(before, "--before")
    _object_id(after, "--after")
    if DELETED.fullmatch(after):
        return None
    _require(worktree, after)
    if DELETED.fullmatch(before):
        why = "The push created its branch"
    elif not _is_commit(worktree, before):
        why = f"The clone lacks {before}, the commit the push replaced"
    else:
        ancestor = _git(worktree, "merge-base", "--is-ancestor", before, after)
        if ancestor.returncode == 0:
            return Scope(
                (before, after),
                f"{before}..{after}",
                after,
                f"The gate checks the files and commits that the push from "
                f"{before} to {after} adds.",
            )
        why = f"The push rewrote history: {before} is not an ancestor of {after}"
    return Scope(
        None,
        after,
        after,
        f"{why}, so the gate checks every file and scans every commit "
        f"reachable from {after}.",
    )


def require_checked_out(worktree: Worktree, head: str) -> None:
    """Refuse a worktree that does not hold exactly the committed head.

    pre-commit reads the files a range names from the worktree, so a scope
    that names a head checks that commit only when it is checked out unchanged.
    """
    checked_out = _git(
        worktree, "rev-parse", "--verify", "HEAD^{commit}", answers=False
    )
    if checked_out.returncode or checked_out.stdout.decode().strip() != head:
        raise GatesealError(
            "head_not_checked_out",
            f"The gate checks {head}, but the worktree has another commit checked out.",
            next_step=f"Check out {head}, then run gateseal gate again.",
        )
    # The index can differ from HEAD while the worktree matches HEAD, and
    # pre-commit would then stash the worktree and check the index. A textconv
    # or external diff driver could report two different files as equal.
    raw = ["--quiet", "--ignore-submodules", "--no-textconv", "--no-ext-diff"]
    for against in (["--cached", "HEAD"], []):
        if _git(worktree, "diff", *raw, *against).returncode == 1:
            raise GatesealError(
                "uncommitted_changes",
                f"The gate checks the committed {head}, but the worktree or the "
                "index changes it.",
                next_step="Commit or stash the changes, then run gateseal gate again.",
            )


def _object_id(revision: str, flag: str) -> str:
    if not OBJECT_ID.fullmatch(revision):
        raise GatesealError(
            "invalid_invocation",
            f"{flag} needs a full commit ID of 40 or 64 lowercase hexadecimal digits.",
        )
    return revision


def _git(
    worktree: Worktree, *arguments: str, answers: bool = True
) -> subprocess.CompletedProcess[bytes]:
    """Run git in the worktree.

    A command that answers yes or no exits 0 or 1, so any other status is a
    tool failure.
    """
    result = execute(
        [worktree.git, *arguments],
        worktree.root,
        clean_environment() | OFFLINE,
        allow_failure=True,
    )
    if answers and result.returncode not in (0, 1):
        detail = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        raise GatesealError("tool_failed", f"git {arguments[0]} failed: {detail}", 4)
    return result


def _is_commit(worktree: Worktree, revision: str) -> bool:
    found = _git(worktree, "cat-file", "-e", revision + "^{commit}", answers=False)
    return found.returncode == 0


def _require(worktree: Worktree, revision: str) -> None:
    if not _is_commit(worktree, revision):
        raise GatesealError(
            "unknown_revision",
            f"{revision} is not a commit in this clone.",
            next_step="Check out the repository with its full history, as "
            "actions/checkout does with fetch-depth: 0, then run gateseal gate "
            "again.",
        )
