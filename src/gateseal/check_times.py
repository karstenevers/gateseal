"""Time each check of the gate, name the slowest, and warn past the budget.

pre-commit prints a hook's name before it runs the hook, and the hook's
status after it, so the time of a status line is the time of its hook.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from gateseal.diagnostics import COLOR, Note
from gateseal.hook_results import STATUS
from gateseal.processes import Timed
from gateseal.tools.model import Hook

HEADING = "Time of each check, in seconds:"
SETUP = "pre-commit setup, such as installing hooks"
"""The row of pre-commit's own time, as it starts and installs hook environments."""


@dataclass(frozen=True)
class CheckTime:
    """The seconds that one check of the gate took, such as a hook or a scan."""

    name: str
    seconds: float


def gate_time(
    pre_commit: Timed,
    hooks: Iterable[Hook],
    scans: Sequence[CheckTime],
    budget: int | None,
) -> tuple[dict[str, object], tuple[str, ...], tuple[Note, ...]]:
    """Return the gate's duration entry, its lines of check times and its warnings.

    The gate's time is pre-commit's and each history scan's, to one decimal,
    and only past the budget does it warn. A hook that pre-commit skipped is
    no check, and neither is pre-commit's setup, so neither is the slowest.
    When no check ran, there are no lines and the entry names no slowest.
    """
    ran = _hook_times(pre_commit, {hook.name for hook in hooks})
    checks = [*ran, *scans]
    seconds = round(pre_commit.seconds + sum(scan.seconds for scan in scans), 1)
    took = f"The gate took {seconds:.1f} seconds"
    slowest = max(checks, key=lambda check: check.seconds, default=None)
    named = (
        ""
        if slowest is None
        else f" The slowest check took {slowest.seconds:.1f} seconds: {slowest.name}."
    )
    message = f"{took}.{named}"
    entry: dict[str, object] = {
        "severity": "info",
        "category": "duration",
        "seconds": seconds,
        "message": message,
    }
    setup = CheckTime(SETUP, pre_commit.seconds - sum(hook.seconds for hook in ran))
    lines = (
        ()
        if slowest is None
        else (
            HEADING,
            *(f"{check.seconds:7.1f}  {check.name}" for check in (setup, *checks)),
            message,
        )
    )
    if budget is None:
        return entry, lines, ()
    entry["budget_seconds"] = budget
    if seconds <= budget:
        return entry, lines, ()
    warning = (
        f"{took}, over the policy's time budget of {budget} seconds. Speed up its "
        f"slowest checks, or raise time_budget_seconds in the policy.{named}"
    )
    return entry, lines, (Note("warning", "over_time_budget", warning),)


def _hook_times(pre_commit: Timed, names: set[str]) -> list[CheckTime]:
    """Return a CheckTime for each hook that passed or failed, in run order."""
    times: list[CheckTime] = []
    for line, seconds in pre_commit.lines:
        text = COLOR.sub("", line.decode("utf-8", errors="replace"))
        status = STATUS.fullmatch(text)
        if status and status["status"] != "Skipped" and status["name"] in names:
            times.append(CheckTime(status["name"], seconds))
    return times
