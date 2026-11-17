"""Run the selected checks with pre-commit: gateseal run and the gate.

Both start the pre-commit that gateseal setup installed, never one from the
PATH, so a clone and CI run the same pre-commit. pre-commit gets the caller's
environment, as it does at git commit.
"""

import os
import re
import subprocess
from dataclasses import replace
from time import monotonic

from gateseal import ci, lock, setup
from gateseal.ci import OFFLINE, Scope
from gateseal.compiler_release import CHECK_HOOK
from gateseal.diagnostics import Note, Result
from gateseal.hook_results import COLOR, FINDING, hook_results
from gateseal.managed_files import CONFIG
from gateseal.processes import execute, pre_commit_command
from gateseal.tools.model import HOOK_STAGES, RANGE, Stage, ToolDefinition
from gateseal.worktree import Worktree

HISTORY_FINDING = re.compile(r"(?P<message>.+) in commit (?P<commit>[0-9a-f]{40,64})")
"""The suffix the history report template gives a finding's message."""

SCAN_MESSAGES = {
    "passed": "passed.",
    "found": "reported findings.",
    "stopped": "stopped without a finding.",
}
"""How a history scan ended, by outcome, as its diagnostic says."""


def run(locked: lock.Locked) -> Result:
    """Run the hooks of a commit on every file.

    The check hook reads the index that a commit records. gateseal run checks
    the working tree, whose files lock.load compared with the Lock, so pre-commit
    skips that hook here.
    """
    environment = dict(os.environ)
    environment["SKIP"] = ",".join(filter(None, (environment.get("SKIP"), CHECK_HOOK)))
    process = execute(
        pre_commit_command(
            setup.require_pre_commit(),
            [*hook_run(Stage.RUN), "--all-files", "--color", "never"],
        ),
        locked.root,
        environment,
        allow_failure=True,
    )
    details = (process.stdout + process.stderr).decode("utf-8", errors="replace")
    entries = tuple(hook_results(details, locked.selection.hooks, "run"))
    if process.returncode == 0:
        return Result(
            "run", "checks_passed", "Checks passed.", details=details, entries=entries
        )
    if process.returncode == 1:
        steps: list[str] = []
        if any(entry["category"] == "finding" for entry in entries):
            steps.append("Fix each finding at its file and line.")
        if any(entry.get("modified") for entry in entries):
            steps.append("Review the files that hooks changed with git diff.")
        steps.append("Stage the fixes, then run gateseal run again.")
        return Result(
            "run",
            "checks_failed",
            "Checks failed or modified files.",
            1,
            next_steps=tuple(steps),
            details=details,
            entries=entries,
        )
    return Result(
        "run",
        "tool_failed",
        f"pre-commit stopped with status {process.returncode}.",
        4,
        next_steps=("gateseal doctor",),
        details=details,
        entries=entries,
    )


def gate(worktree: Worktree, locked: lock.Locked, scope: Scope | None) -> Result:
    """Run the ci stage's hooks on scope's files, then each history scan.

    pre-commit reads the files from the worktree, so a scope that names a head
    needs that commit checked out with no uncommitted changes. That also means
    a ranged run, which pre-commit would otherwise stash as a commit does,
    finds no unstaged edits.
    """
    if scope is None:
        return Result(
            "gate",
            "nothing_to_check",
            "The event deleted its ref, so the gate has nothing to check.",
        )
    files = (
        ["--all-files"]
        if scope.files is None
        else ["--from-ref", scope.files[0], "--to-ref", scope.files[1]]
    )
    python = setup.require_pre_commit()
    if scope.head is not None:
        ci.require_checked_out(worktree, scope.head)
    env = dict(os.environ) | OFFLINE
    started = monotonic()
    process = execute(
        pre_commit_command(python, [*hook_run(Stage.CI), *files, "--color", "never"]),
        locked.root,
        env,
        allow_failure=True,
    )
    revisions = scope.history
    scans: list[tuple[ToolDefinition, subprocess.CompletedProcess[bytes]]] = []
    for tool in locked.selection.tools:
        definition, history = tool.definition, tool.definition.history
        program = tool.program(OFFLINE)
        if revisions is not None and history is not None and program is not None:
            scanned = execute(
                [*program, *(a.replace(RANGE, revisions) for a in history)],
                locked.root,
                env,
                allow_failure=True,
            )
            scans.append((definition, scanned))
    duration, over = _duration(monotonic() - started, locked.policy.time_budget_seconds)
    details = (process.stdout + process.stderr).decode("utf-8", errors="replace")
    entries = [duration, *hook_results(details, locked.selection.hooks, "ci")]
    outcomes: set[str] = set()
    for definition, scanned in scans:
        output = scanned.stdout.decode("utf-8", errors="replace")
        details += output + scanned.stderr.decode("utf-8", errors="replace")
        findings = _history_findings(definition, output)
        outcome = _scan_outcome(scanned.returncode, findings)
        outcomes.add(outcome)
        entries.append(_scan_entry(definition, revisions, outcome))
        entries += findings
    result = _gate_result(scope.reason, process.returncode, outcomes, details, entries)
    return replace(result, notes=over)


def _duration(
    elapsed: float, budget: int | None
) -> tuple[dict[str, object], tuple[Note, ...]]:
    """Return the gate's time to one decimal, and a warning if it broke the budget."""
    seconds = round(elapsed, 1)
    took = f"The gate took {seconds:.1f} seconds"
    entry: dict[str, object] = {
        "severity": "info",
        "category": "duration",
        "seconds": seconds,
        "message": f"{took}.",
    }
    if budget is None:
        return entry, ()
    entry["budget_seconds"] = budget
    if seconds <= budget:
        return entry, ()
    warning = (
        f"{took}, over the policy's time budget of {budget} seconds. Speed up its "
        "slowest checks, or raise time_budget_seconds in the policy."
    )
    return entry, (Note("warning", "over_time_budget", warning),)


def _scan_outcome(returncode: int, findings: list[dict[str, object]]) -> str:
    # Betterleaks exits 1 on an error too, so only a finding line is one.
    return (
        "passed"
        if returncode == 0 and not findings
        else "found"
        if returncode == 1 and findings
        else "stopped"
    )


def _scan_entry(
    definition: ToolDefinition, revisions: str | None, outcome: str
) -> dict[str, object]:
    name = definition.name
    return {
        "severity": "info" if outcome == "passed" else "error",
        "category": "history",
        "tool": name,
        "origin": str(definition.origin),
        "stage": "ci",
        "status": "passed" if outcome == "passed" else "failed",
        "message": f"{name} scanned the commits {revisions}: " + SCAN_MESSAGES[outcome],
    }


def _gate_result(
    reason: str,
    returncode: int,
    outcomes: set[str],
    details: str,
    entries: list[dict[str, object]],
) -> Result:
    if returncode not in (0, 1) or "stopped" in outcomes:
        stopped = (
            f"pre-commit stopped with status {returncode}."
            if returncode not in (0, 1)
            else "A history scan stopped without a finding."
        )
        return Result(
            "gate",
            "tool_failed",
            f"{stopped} {reason}",
            4,
            next_steps=("gateseal doctor",),
            details=details,
            entries=tuple(entries),
        )
    if returncode == 0 and "found" not in outcomes:
        return Result(
            "gate",
            "checks_passed",
            f"Checks passed. {reason}",
            details=details,
            entries=tuple(entries),
        )
    steps: list[str] = []
    if any(entry["category"] == "finding" for entry in entries):
        steps.append("Fix each finding at its file and line.")
    if any(entry.get("modified") for entry in entries):
        steps.append(
            "Run gateseal run in a clone, review and commit what the hooks change, "
            "then push."
        )
    if not steps:
        steps.append(
            "Read the output of each failed hook, fix what it reports, then push again."
        )
    return Result(
        "gate",
        "checks_failed",
        f"Checks failed or modified files. {reason}",
        1,
        next_steps=tuple(steps),
        details=details,
        entries=tuple(entries),
    )


def _history_findings(tool: ToolDefinition, output: str) -> list[dict[str, object]]:
    """Return a finding for each file:line:column: rule message in commit line."""
    findings: list[dict[str, object]] = []
    for line in COLOR.sub("", output).splitlines():
        found = FINDING.fullmatch(line)
        suffix = None if found is None else HISTORY_FINDING.fullmatch(found["message"])
        if found is None or suffix is None:
            continue
        findings.append(
            {
                "severity": "error",
                "category": "finding",
                "tool": tool.name,
                "origin": str(tool.origin),
                "hook": "history",
                "stage": "ci",
                "file": found["file"],
                "line": int(found["line"]),
                "column": int(found["column"]),
                "rule": found["rule"],
                "message": suffix["message"],
                "commit": suffix["commit"],
            }
        )
    return findings


def hook_run(stage: Stage) -> list[str]:
    """Return pre-commit's run arguments for the hooks of one Stage."""
    return ["run", "--config", CONFIG, "--hook-stage", HOOK_STAGES[stage]]
