"""What gateseal status and gateseal doctor check in a clone.

Both commands report the same four checks: the Lock, the pre-commit that
gateseal setup installs, the clone's commit launcher, and mise. status only
observes, so it uses no network and always exits 0. doctor fetches a Policy
that the clone has not stored, fails when a check fails, and warns about
settings that keep a hook from running as the Lock says.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gateseal import lock, runtime, setup
from gateseal.bootstrap import MANIFEST
from gateseal.diagnostics import GatesealError, Note, Result
from gateseal.environments import tool_environment
from gateseal.tools.release_binary import (
    MINIMUM_MISE,
    BinaryTool,
    mise_problem,
    require_mise,
)
from gateseal.worktree import Worktree

NOT_STORED = frozenset({"policy_unavailable", "policy_content_mismatch"})
"""The codes of a Lock whose Policy this clone has not stored intact."""

REFRESH = "gateseal setup --refresh"

AUTO_INSTALL = ("MISE_AUTO_INSTALL", "MISE_EXEC_AUTO_INSTALL")
"""The variables that turn off mise's install of a tool at its first use."""

OFF = frozenset({"0", "false", "no"})
"""The values that turn a mise setting off, in any case and with spaces around them."""

LABELS = {
    "lock": "Lock",
    "pre-commit": "pre-commit",
    "launcher": "Launcher",
    "mise": "mise",
}


@dataclass(frozen=True)
class Check:
    """One check's outcome, with the step that repairs a failed one."""

    name: str
    status: Literal["passed", "failed", "skipped"]
    message: str
    next_step: str = ""

    def entry(self) -> dict[str, object]:
        return {
            "severity": "error" if self.status == "failed" else "info",
            "category": "check",
            "check": self.name,
            "status": self.status,
            "message": self.message,
        }


def load(worktree: Worktree) -> lock.Locked:
    """Load the Lock, and fetch its Policy first when the clone lacks it."""
    root, preparation = worktree.root, worktree.preparation
    try:
        return lock.load(root, preparation)
    except GatesealError as exc:
        if exc.code not in NOT_STORED:
            raise
    with runtime.operation_lock(runtime.preparation_directory(worktree)):
        lock.ensure_policy(root, preparation, worktree.git)
    return lock.load(root, preparation)


def _lock(worktree: Worktree, fetch: bool) -> tuple[Check, lock.Locked | None]:
    try:
        locked = (
            load(worktree) if fetch else lock.load(worktree.root, worktree.preparation)
        )
    except GatesealError as exc:
        return Check("lock", "failed", str(exc), exc.next_step), None
    return (
        Check(
            "lock",
            "passed",
            "The Manifest, the Lock and the owned files agree with the Policy.",
        ),
        locked,
    )


def _pre_commit() -> Check:
    try:
        python = setup.require_pre_commit()
    except GatesealError as exc:
        return Check("pre-commit", "failed", str(exc), exc.next_step)
    return Check("pre-commit", "passed", f"The pre-commit of {python} starts.")


def _content(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _launcher(worktree: Worktree) -> Check:
    hook = worktree.hook
    found = _content(hook)
    if found is None:
        problem = f"{hook} is missing, so git commit runs no hook."
    elif found != _content(setup.Layout(setup.data_root()).launcher):
        problem = f"{hook} is not the commit launcher that gateseal setup installs."
    elif not os.access(hook, os.X_OK):
        problem = f"{hook} is not executable, so git commit skips it."
    else:
        return Check("launcher", "passed", f"{hook} is the commit launcher.")
    return Check("launcher", "failed", problem, REFRESH)


def _mise(locked: lock.Locked | None) -> Check:
    if locked is None:
        return Check(
            "mise", "skipped", "The Lock did not load, so its tools are unknown."
        )
    if not _selects_release_binary(locked):
        return Check("mise", "skipped", "No selected tool is a release binary.")
    try:
        mise = require_mise(tool_environment())
    except GatesealError as exc:
        return Check(
            "mise",
            "failed",
            mise_problem("A release binary starts through", exc),
            f"Install mise {MINIMUM_MISE} or later.",
        )
    return Check("mise", "passed", f"{mise} is mise {MINIMUM_MISE} or later.")


def _selects_release_binary(locked: lock.Locked) -> bool:
    return any(isinstance(tool, BinaryTool) for tool in locked.selection.tools)


def _checks(
    worktree: Worktree, fetch: bool
) -> tuple[tuple[Check, ...], lock.Locked | None]:
    found, locked = _lock(worktree, fetch)
    return (found, _pre_commit(), _launcher(worktree), _mise(locked)), locked


def _warnings(worktree: Worktree, locked: lock.Locked | None) -> list[tuple[Note, str]]:
    """Return each warning with its step: settings that no check fails on."""
    warnings: list[tuple[Note, str]] = []
    folder = setup.git_setting(worktree.git, worktree.root, "core.hooksPath")
    if folder is not None:
        warning = Note(
            "warning",
            "custom_hooks_path",
            f"Git's core.hooksPath names {folder}, so git commit skips the commit "
            "launcher.",
        )
        warnings.append((warning, "Remove the core.hooksPath setting."))
    off = [
        name for name in AUTO_INSTALL if os.environ.get(name, "").strip().lower() in OFF
    ]
    if off and locked is not None and _selects_release_binary(locked):
        warning = Note(
            "warning",
            "mise_auto_install_off",
            f"mise's install at first use is off through {' and '.join(off)}, "
            "so a hook starts an executable of the same name from the PATH when "
            "mise has not installed its tool.",
        )
        warnings.append((warning, f"Unset {' and '.join(off)}."))
    return warnings


def _result(
    command: str,
    checks: tuple[Check, ...],
    locked: lock.Locked | None,
    warnings: list[tuple[Note, str]],
    failure: int,
) -> Result:
    """Report checks; a failed one gives the code unhealthy and exits failure."""
    failed = [check for check in checks if check.status == "failed"]
    notes = () if locked is None else lock.describe(locked)
    steps = [check.next_step for check in failed if check.next_step]
    steps += [step for _, step in warnings]
    return Result(
        command,
        "unhealthy" if failed else "healthy",
        f"{len(failed)} of {len(checks)} checks failed."
        if failed
        else "Every check passed.",
        failure if failed else 0,
        next_steps=tuple(dict.fromkeys([*steps, *lock.review_steps(notes)])),
        details="\n".join(
            f"{LABELS[check.name]}: {check.status}. {check.message}" for check in checks
        ),
        notes=(*(note for note, _ in warnings), *notes),
        entries=tuple(check.entry() for check in checks),
    )


def status(worktree: Worktree) -> Result:
    """Report the checks without the network and without a failing exit code."""
    if not (worktree.root / MANIFEST).exists():
        return Result(
            "status",
            "unconfigured",
            "No gateseal manifest exists.",
            next_steps=("gateseal configure --help",),
        )
    checks, locked = _checks(worktree, fetch=False)
    return _result("status", checks, locked, [], 0)


def doctor(worktree: Worktree) -> Result:
    """Report the checks and the warnings, and fail when a check fails."""
    checks, locked = _checks(worktree, fetch=True)
    return _result("doctor", checks, locked, _warnings(worktree, locked), 3)
