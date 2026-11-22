"""Run the selected checks with pre-commit: gateseal run and the gate.

Both start the pre-commit that gateseal setup installed, never one from the
PATH, so a clone and CI run the same pre-commit. pre-commit gets the caller's
environment, as it does at git commit.
"""

import os
import re
import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import replace

from gateseal import ci, lock, setup
from gateseal.check_times import CheckTime, gate_time
from gateseal.ci import OFFLINE, Scope
from gateseal.compiler_release import CHECK_HOOK
from gateseal.diagnostics import COLOR, Result
from gateseal.environments import clean_environment
from gateseal.hook_results import FINDING, hook_results
from gateseal.managed_files import CONFIG
from gateseal.processes import Timed, execute, execute_timed, pre_commit_command
from gateseal.tools import reports
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

MISSING_REPORT = (
    "Read the output of the hook that writes the report and fix what it reports, "
    "then push again. If pre-commit skipped that hook, give both hooks the same "
    "file types in the policy."
)
"""The next step of a gate whose hook ran without a report that it reads."""

STOPPED_HOOK = (
    "Read the output of each stopped hook and fix what stopped it, such as "
    "network access to its service."
)
"""The next step of a run whose hook stopped with an exit code of no finding."""


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
    failure = _tool_failure(process.returncode, entries)
    if failure is not None:
        message, step = failure
        return Result(
            "run",
            "tool_failed",
            message,
            4,
            next_steps=(step,),
            details=details,
            entries=entries,
        )
    if process.returncode == 0:
        return Result(
            "run", "checks_passed", "Checks passed.", details=details, entries=entries
        )
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


def gate(
    worktree: Worktree, locked: lock.Locked, scope: Scope | None, in_ci: bool = False
) -> Result:
    """Run the ci stage's hooks on scope's files, then each history scan.

    pre-commit reads the files from the worktree, so a scope that names a head
    needs that commit checked out with no uncommitted changes. That also means
    a ranged run, which pre-commit would otherwise stash as a commit does,
    finds no unstaged edits. The reports of an earlier run go first, and a
    hook that then ran without a report it reads ends the gate with
    missing_report. in_ci asks pre-commit for color, which a CI log shows,
    and adds the diff of what the hooks changed after pre-commit's output.
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
    clean = _diff(worktree, "--quiet").returncode == 0 if in_ci else None
    reports.clear(locked.root, locked.selection.hooks)
    pre_commit = execute_timed(
        pre_commit_command(
            python,
            [*hook_run(Stage.CI), *files, "--color", "always" if in_ci else "never"],
        ),
        locked.root,
        env,
    )
    process = pre_commit.process
    revisions = scope.history
    scans = _scan_history(locked, revisions, env)
    duration, times, over = gate_time(
        pre_commit,
        locked.selection.hooks,
        [CheckTime(f"{d.name} history scan", timed.seconds) for d, timed in scans],
        locked.policy.time_budget_seconds,
    )
    details = (process.stdout + process.stderr).decode("utf-8", errors="replace")
    results = hook_results(details, locked.selection.hooks, "ci")
    if clean is not None and any(entry.get("modified") for entry in results):
        details += _changes(worktree) if clean else EDITED
    missing = reports.missing(locked.root, locked.selection.reads, results)
    entries = [duration, *results]
    outcomes: set[str] = set()
    for definition, timed in scans:
        scanned = timed.process
        output = scanned.stdout.decode("utf-8", errors="replace")
        details += output + scanned.stderr.decode("utf-8", errors="replace")
        findings = _history_findings(definition, output)
        outcome = _scan_outcome(scanned.returncode, findings)
        outcomes.add(outcome)
        entries.append(_scan_entry(definition, revisions, outcome))
        entries += findings
    result = _gate_result(
        scope.reason, process.returncode, outcomes, missing, details, entries
    )
    return replace(result, notes=over, check_times=times)


def _scan_history(
    locked: lock.Locked, revisions: str | None, env: dict[str, str]
) -> list[tuple[ToolDefinition, Timed]]:
    """Run and time each history scan of the selected tools on revisions, if any."""
    scans: list[tuple[ToolDefinition, Timed]] = []
    for tool in locked.selection.tools:
        definition, history = tool.definition, tool.definition.history
        program = tool.program(OFFLINE)
        if revisions is not None and history is not None and program is not None:
            scanned = execute_timed(
                [*program, *(a.replace(RANGE, revisions) for a in history)],
                locked.root,
                env,
            )
            scans.append((definition, scanned))
    return scans


EDITED = (
    "\nThe worktree had changes before the hooks ran, so the log shows no diff of "
    "what the hooks changed.\n"
)
"""What a CI gate's log says instead of the diff when a full gate starts with
changes, which the diff could not tell apart from the hooks' changes."""


def _changes(worktree: Worktree) -> str:
    """Return a header and the colored diff of what the hooks changed, for the log.

    A CI run cannot show the change to its reader any other way.
    """
    diff = _diff(worktree, "--color=always")
    output = (diff.stdout + diff.stderr).decode("utf-8", errors="replace")
    return "\nChanges that the hooks made:\n" + output


def _diff(worktree: Worktree, flag: str) -> subprocess.CompletedProcess[bytes]:
    """Run git diff of the worktree against the index with flag, as the files
    are, without a diff driver or a submodule's changes."""
    return execute(
        [
            worktree.git,
            "diff",
            "--ignore-submodules",
            "--no-textconv",
            "--no-ext-diff",
            flag,
        ],
        worktree.root,
        clean_environment() | OFFLINE,
        allow_failure=True,
    )


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
    missing: str | None,
    details: str,
    entries: list[dict[str, object]],
) -> Result:
    """Return the gate's result: a stopped tool first, then a missing report."""
    failure = _tool_failure(returncode, entries)
    if failure is None and "stopped" in outcomes:
        failure = "A history scan stopped without a finding.", "gateseal doctor"
    if failure is not None:
        message, step = failure
        return Result(
            "gate",
            "tool_failed",
            f"{message} {reason}",
            4,
            next_steps=(step,),
            details=details,
            entries=tuple(entries),
        )
    if missing is not None:
        return Result(
            "gate",
            "missing_report",
            f"{missing} {reason}",
            2,
            next_steps=(MISSING_REPORT,),
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


def _tool_failure(
    returncode: int, entries: Iterable[Mapping[str, object]]
) -> tuple[str, str] | None:
    """Return why pre-commit or a hook stopped without checking, and the next step.

    A hook that stopped with an exit code of no finding counts when pre-commit
    exits 1, as it does for any failed hook. Any other status but 0 is
    pre-commit's own failure. Return None when neither stopped.
    """
    stopped = " ".join(
        f"The {entry['hook']} hook stopped with exit code {entry['exit_code']}."
        for entry in entries
        if entry["category"] == "hook" and entry["status"] == "stopped"
    )
    if returncode == 1 and stopped:
        return stopped, STOPPED_HOOK
    if returncode not in (0, 1):
        return f"pre-commit stopped with status {returncode}.", "gateseal doctor"
    return None


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
