"""Read pre-commit's report of a run as one result per hook and its findings."""

import re
from collections.abc import Iterable

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
# Inherited settings such as PRE_COMMIT_COLOR or FORCE_COLOR can color the
# output even when it is captured.
COLOR = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def hook_results(
    output: str, hooks: Iterable[Hook], stage: str
) -> list[dict[str, object]]:
    """Return a diagnostic for each hook pre-commit reported, then its findings.

    pre-commit prints one status line per hook, named as the configuration
    names it, and a failed hook's output below it. Only hooks whose output is
    concise, file:line:column: rule message lines, yield findings. Ruff names
    a notebook finding's cell after the file, as file:cell N:line:column.
    """
    names = {hook.name: hook for hook in hooks}
    results: list[dict[str, object]] = []
    hook: Hook | None = None
    current: dict[str, object] = {}
    for line in COLOR.sub("", output).splitlines():
        status = STATUS.fullmatch(line)
        if status is not None and status["name"] in names:
            hook = names[status["name"]]
            current = _hook_result(hook, status, stage)
            results.append(current)
            continue
        if hook is None:
            continue
        if line == MODIFIED:
            current["modified"] = True
            current["message"] = f"{current['message']}, {MODIFIED[2:]}"
        elif hook.output is OutputFormat.CONCISE and (found := FINDING.fullmatch(line)):
            results.append(_finding(hook, found, stage))
    return results


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
