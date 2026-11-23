"""Reports: the files that one hook writes for a later hook to read.

A hook declares the reports it reads and writes by name, and report_path
gives each its path. This module holds what follows from those declarations
for a whole policy or selection: which hook writes the report that another
reads, whether it runs before the reader wherever the reader runs, the owned
output that keeps the reports out of Git, what the gate does so that no hook
passes on a report of an earlier run or on none, and what check and status
say about each report.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from gateseal.diagnostics import GatesealError, Note
from gateseal.tools.model import (
    AT_COMMIT,
    Hook,
    Stage,
    ToolDefinition,
    ToolUse,
    report_path,
)

WHEN: Mapping[frozenset[Stage], str] = {
    AT_COMMIT: "at a commit, in gateseal run and in the gate",
    frozenset({Stage.PUSH, Stage.CI}): "at a push and in the gate",
    frozenset({Stage.CI}): "in the gate",
}
"""When the hooks of a tool run, by its stages, as a message says it."""

RAN = ("passed", "failed")
"""The statuses of a hook that ran to a result. A stopped hook makes the gate a
tool failure before it looks for a missing report."""

IGNORE = report_path(".gitignore")
"""The owned output with which Git ignores every report, itself excepted."""


@dataclass(frozen=True)
class Read:
    """One report that a hook reads, and the hook before it that writes it.

    written holds the stages of the writer's tool, which include every stage
    of the reader's tool.
    """

    reader: Hook
    report: str
    writer: Hook
    written: frozenset[Stage]


def reads(
    uses: Iterable[tuple[ToolDefinition, ToolUse]], next_step: str = ""
) -> tuple[Read, ...]:
    """Return each report that a hook reads, in configuration order.

    uses holds each tool's definition and its use, in that order. An earlier
    hook must write the report, in a tool whose stages include every stage of
    the reader's tool. Otherwise the reader could run without its report, and
    the call fails with missing_report. Of several such hooks the last counts,
    since the reader finds its report.
    """
    writers: list[tuple[str, Hook, frozenset[Stage]]] = []
    found: list[Read] = []
    for definition, use in uses:
        for hook in use.hooks:
            for name in hook.reads:
                written = [
                    (writer, stages)
                    for report, writer, stages in writers
                    if report == name and definition.stages <= stages
                ]
                if not written:
                    raise GatesealError(
                        "missing_report",
                        f"Hook {hook.id} reads {report_path(name)}, but no hook "
                        "before it writes that report "
                        f"{WHEN[definition.stages]}.",
                        next_step=next_step,
                    )
                found.append(Read(hook, name, *written[-1]))
            writers += [(name, hook, definition.stages) for name in hook.writes]
    return tuple(found)


def notes(reads: Iterable[Read]) -> tuple[Note, ...]:
    """Name each report that a hook reads and the hook that writes it."""
    return tuple(
        Note(
            "info",
            "report",
            f"Hook {read.reader.id} reads {report_path(read.report)}, which "
            f"{read.writer.id} writes before it {WHEN[read.written]}.",
        )
        for read in reads
    )


def ignore_file(hooks: Iterable[Hook]) -> dict[str, bytes]:
    """Return the owned IGNORE, by its path, when one of hooks writes a report.

    The repository's own .gitignore then needs no entry for the reports. A
    selection that writes none owns no such file.
    """
    return {IGNORE: b"*\n!.gitignore\n"} if any(h.writes for h in hooks) else {}


def clear(root: Path, hooks: Iterable[Hook]) -> None:
    """Remove each report that one of hooks writes, as an earlier run left it.

    A report of an earlier run can then not pass for this run. Every other
    entry of the report folder stays.
    """
    for hook in hooks:
        for name in hook.writes:
            (root / report_path(name)).unlink(missing_ok=True)


def missing(
    root: Path, reads: Iterable[Read], results: Iterable[Mapping[str, object]]
) -> str | None:
    """Say which hook ran without a report that it reads, or return None.

    results holds pre-commit's result of each hook, and the findings in its
    output. A hook that pre-commit skipped read nothing, so it needs no
    report. When the writing hook failed, its own output says why the report
    is missing, and the gate fails as a failed check.
    """
    status = {
        result["hook"]: result["status"]
        for result in results
        if result["category"] == "hook"
    }
    for read in reads:
        path = report_path(read.report)
        if (
            status.get(read.reader.id) in RAN
            and status.get(read.writer.id) != "failed"
            and not (root / path).is_file()
        ):
            return (
                f"Hook {read.reader.id} ran, but {path}, which {read.writer.id} "
                "writes before it, is missing."
            )
    return None
