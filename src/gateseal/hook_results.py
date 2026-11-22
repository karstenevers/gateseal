"""Read pre-commit's report of a run as one result per hook and its findings."""

import re
from collections.abc import Iterable

from gateseal.diagnostics import COLOR
from gateseal.tools.model import Hook, OutputFormat

STATUS = re.compile(
    r"(?P<name>.+?)\.+(?:\((?P<reason>[^()]*)\))?(?P<status>Passed|Failed|Skipped)"
)
FINDING = re.compile(
    r"(?P<file>.+?)(?::cell (?P<cell>[0-9]+))?:(?P<line>[0-9]+):(?P<column>[0-9]+): "
    r"(?P<rule>\S+?):? (?:\[\*\] )?(?P<message>.+)"
)
"""A concise finding. A team's Betterleaks rule may name itself with any
characters but white space, such as aws_access_token, 123-token or ns:token."""
MODIFIED = "- files were modified by this hook"
EXIT_CODE = re.compile(r"- exit code: (?P<code>-?[0-9]+)")
"""A failed hook's exit code, negative for a process that a signal ended."""


def hook_results(
    output: str, hooks: Iterable[Hook], stage: str
) -> list[dict[str, object]]:
    """Return a diagnostic for each hook pre-commit reported, then its findings.

    pre-commit prints one status line per hook, named as the configuration
    names it, and a failed hook's output below it. Only hooks whose output is
    concise, file:line:column: rule message lines, yield findings. Ruff names
    a notebook finding's cell after the file, as file:cell N:line:column.
    Right below a failed hook's status line, pre-commit prints its own lines
    about the hook, each starting with "- ", and the hook's output follows
    after a blank line. Only those lines name the exit code and modified files.
    """
    names = {hook.name: hook for hook in hooks}
    results: list[dict[str, object]] = []
    hook: Hook | None = None
    current: dict[str, object] = {}
    metadata = False  # pytestmut: skip[bool.literal] -- a status line sets it first
    for line in COLOR.sub("", output).splitlines():
        status = STATUS.fullmatch(line)
        if status is not None and status["name"] in names:
            hook = names[status["name"]]
            current = _hook_result(hook, status, stage)
            results.append(current)
            metadata = True
            continue
        if hook is None:
            continue
        metadata = metadata and line.startswith("- ")
        if metadata:
            _read_metadata(hook, current, line)
        elif hook.output is OutputFormat.CONCISE and (found := FINDING.fullmatch(line)):
            results.append(_finding(hook, found, stage))
    return results


def _read_metadata(hook: Hook, current: dict[str, object], line: str) -> None:
    """Record what a line of pre-commit's own about a failed hook says.

    A hook whose definition lists finding exit codes, and whose exit code is
    none of them, stopped: its status says so and names the code.
    """
    if line == MODIFIED:
        current["modified"] = True
        current["message"] = f"{current['message']}, {MODIFIED[2:]}"
    elif (code := _stop_code(hook, line)) is not None:
        current["status"] = "stopped"
        current["exit_code"] = code
        current["message"] = f"{hook.name}: Stopped with exit code {code}"


def _stop_code(hook: Hook, line: str) -> int | None:
    """Return the exit code that line names, if it is none of hook's finding codes."""
    found = EXIT_CODE.fullmatch(line)
    if found is None or not hook.finding_exit_codes:
        return None
    code = int(found["code"])
    return None if code in hook.finding_exit_codes else code


def _hook_result(hook: Hook, status: re.Match[str], stage: str) -> dict[str, object]:
    return {
        "severity": "error" if status["status"] == "Failed" else "info",
        "category": "hook",
        "tool": hook.tool,
        "origin": str(hook.origin),
        "hook": hook.id,
        "stage": stage,
        "status": status["status"].lower(),
        "modified": False,
        "message": f"{status['name']}: {status['status']}"
        + (f" ({status['reason']})" if status["reason"] else ""),
    }


def _finding(hook: Hook, found: re.Match[str], stage: str) -> dict[str, object]:
    finding: dict[str, object] = {
        "severity": "error",
        "category": "finding",
        "tool": hook.tool,
        "origin": str(hook.origin),
        "hook": hook.id,
        "stage": stage,
        "file": found["file"],
        "line": int(found["line"]),
        "column": int(found["column"]),
        "rule": found["rule"],
        "message": found["message"],
    }
    if found["cell"] is not None:
        finding["cell"] = int(found["cell"])
    return finding
