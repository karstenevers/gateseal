"""What gateseal status checks in a clone.

status reports five checks: the Lock, the pre-commit that gateseal setup
installs, the clone's commit launcher, its push launcher, and mise. It warns
about settings that keep a hook from running as the Lock says, and exits 3
when a check fails. It only observes, so it uses no network: a Policy that the
clone has not stored fails the Lock check.
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from gateseal import lock, setup
from gateseal.diagnostics import GatesealError, Note, Result
from gateseal.environments import tool_environment
from gateseal.managed_files import MANIFEST
from gateseal.tools.mise import MINIMUM_MISE, mise_problem, require_mise
from gateseal.tools.model import LAUNCHERS
from gateseal.tools.release_binary import BinaryTool
from gateseal.worktree import Worktree, operation_lock, preparation_directory

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
    "push-launcher": "Push launcher",
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
    with operation_lock(preparation_directory(worktree)):
        lock.ensure_policy(root, preparation, worktree.git)
    return lock.load(root, preparation)


def _lock(worktree: Worktree) -> tuple[Check, lock.Locked | None]:
    try:
        locked = lock.load(worktree.root, worktree.preparation)
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


def _launcher(name: str, hook: Path, expected: bytes | None, event: str) -> Check:
    """Check that hook is the launcher expected, which Git starts at event."""
    found = _content(hook)
    if found is None:
        problem = f"{hook} is missing, so git {event} runs no hook."
    elif found != expected:
        problem = f"{hook} is not the {event} launcher that gateseal setup installs."
    elif not os.access(hook, os.X_OK):
        problem = f"{hook} is not executable, so git {event} skips it."
    else:
        return Check(name, "passed", f"{hook} is the {event} launcher.")
    return Check(name, "failed", problem, REFRESH)


def _unneeded_push_launcher(
    worktree: Worktree, hook: Path, launcher: bytes | None
) -> bool:
    """Tell whether hook is the push launcher while no worktree asks for it."""
    found = _content(hook)
    return (
        found is not None
        and found == launcher
        and not setup.asks_for_push_launcher(worktree)
    )


def _launchers(worktree: Worktree, locked: lock.Locked | None) -> tuple[Check, Check]:
    """Check the commit launcher, and the push launcher a selected hook needs."""
    commit_hook, push_hook = (worktree.hooks / name for name in LAUNCHERS)
    expected = _content(setup.Layout(setup.data_root()).launcher)
    commit = _launcher("launcher", commit_hook, expected, "commit")
    pushed = None if expected is None else setup.push_launcher(expected)
    if locked is None:
        why = "The Lock did not load, so its hooks are unknown."
    elif locked.selection.pushes:
        return commit, _launcher("push-launcher", push_hook, pushed, "push")
    elif _unneeded_push_launcher(worktree, push_hook, pushed):
        why = (
            f"No selected hook runs at a push, so the push launcher {push_hook} runs "
            "no check. gateseal setup --refresh removes it, so that Git LFS can "
            "write its push hook."
        )
    else:
        why = "No selected hook runs at a push."
    return commit, Check("push-launcher", "skipped", why)


def _mise(locked: lock.Locked | None) -> Check:
    if locked is None:
        return Check(
            "mise", "skipped", "The Lock did not load, so its tools are unknown."
        )
    if not any(_mise_installs(locked)):
        return Check("mise", "skipped", "No selected tool starts through mise.")
    try:
        mise = require_mise(tool_environment())
    except GatesealError as exc:
        return Check(
            "mise",
            "failed",
            mise_problem("A release binary or the policy's Node starts through", exc),
            f"Install mise {MINIMUM_MISE} or later.",
        )
    return Check("mise", "passed", f"{mise} is mise {MINIMUM_MISE} or later.")


def _mise_installs(locked: lock.Locked) -> tuple[bool, bool]:
    """Say whether mise installs a release binary, and the policy's Node, for
    the selection."""
    tools = locked.selection.tools
    binary = any(isinstance(tool, BinaryTool) for tool in tools)
    return binary, any(tool.node for tool in tools)


def _without_install(binary: bool, node: bool) -> str:
    """Say what a hook does when mise has not installed what it starts.

    mise then starts an executable of the same name from the PATH. That holds
    for a release binary. A hook on Node asks mise for its install and stops
    without it.
    """
    effects: list[str] = []
    if binary:
        effects.append(
            "a hook starts an executable of the same name from the PATH when "
            "mise has not installed its tool"
        )
    if node:
        effects.append(
            "a hook on Node stops when mise has not installed the pinned Node"
        )
    return ", and ".join(effects)


def _checks(worktree: Worktree) -> tuple[tuple[Check, ...], lock.Locked | None]:
    found, locked = _lock(worktree)
    launchers = _launchers(worktree, locked)
    return (found, _pre_commit(), *launchers, _mise(locked)), locked


def _warnings(worktree: Worktree, locked: lock.Locked | None) -> list[tuple[Note, str]]:
    """Return each warning with its step: settings that no check fails on."""
    warnings: list[tuple[Note, str]] = []
    folder = setup.git_setting(worktree.git, worktree.root, "core.hooksPath")
    if folder is not None:
        shown = os.fsencode(folder).decode(errors="replace")
        skipped = "git commit skips the commit launcher"
        if locked is not None and locked.selection.pushes:
            skipped = "git commit and git push skip their launchers"
        warning = Note(
            "warning",
            "custom_hooks_path",
            f"Git's core.hooksPath names {shown}, so {skipped}.",
        )
        warnings.append((warning, "Remove the core.hooksPath setting."))
    off = [
        name for name in AUTO_INSTALL if os.environ.get(name, "").strip().lower() in OFF
    ]
    binary, node = (False, False) if locked is None else _mise_installs(locked)
    if off and (binary or node):
        warning = Note(
            "warning",
            "mise_auto_install_off",
            f"mise's install at first use is off through {' and '.join(off)}, "
            f"so {_without_install(binary, node)}.",
        )
        warnings.append((warning, f"Unset {' and '.join(off)}."))
    return warnings


def status(worktree: Worktree) -> Result:
    """Report the checks and the warnings, and exit 3 when a check fails."""
    if not (worktree.root / MANIFEST).exists():
        return Result(
            "status",
            "unconfigured",
            "No gateseal manifest exists.",
            next_steps=("gateseal configure --help",),
        )
    checks, locked = _checks(worktree)
    warnings = _warnings(worktree, locked)
    failed = [check for check in checks if check.status == "failed"]
    notes = () if locked is None else lock.describe(locked)
    steps = [check.next_step for check in failed if check.next_step]
    steps += [step for _, step in warnings]
    return Result(
        "status",
        "unhealthy" if failed else "healthy",
        f"{len(failed)} of {len(checks)} checks failed."
        if failed
        else "Every check passed.",
        3 if failed else 0,
        next_steps=tuple(dict.fromkeys([*steps, *lock.review_steps(notes)])),
        details="\n".join(
            f"{LABELS[check.name]}: {check.status}. {check.message}" for check in checks
        ),
        notes=(*(note for note, _ in warnings), *notes),
        entries=tuple(check.entry() for check in checks),
    )
