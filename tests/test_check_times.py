"""How the gate reports the time of each check, its slowest, and its budget."""

import subprocess

import pytest
from gateseal.check_times import CheckTime, gate_time
from gateseal.diagnostics import Note
from gateseal.processes import Timed
from gateseal.tools.model import Hook, Origin, OutputFormat

HOOKS = tuple(
    Hook("tool", Origin.BUILT_IN, name, name, (), (), OutputFormat.CONCISE)
    for name in ("check yaml", "Sleep, then refuse FAIL", "fix end of files")
)
YAML = b"check yaml...............................................Passed"
SLEEP = b"Sleep, then refuse FAIL..................................Failed"
SKIPPED = b"fix end of files.......................(no files to check)Skipped"
SCAN = CheckTime("betterleaks history scan", 0.4)
OVER = (
    "The gate took 312.4 seconds, over the policy's time budget of 300 seconds. "
    "Speed up its slowest checks, or raise time_budget_seconds in the policy."
)


def pre_commit(seconds, *lines):
    return Timed(subprocess.CompletedProcess([], 0, b"", b""), seconds, lines)


def test_each_check_is_listed_with_its_time_and_the_slowest_is_named():
    timed = pre_commit(3.3, (YAML, 0.1), (b"- hook id: slow", 0.0), (SLEEP, 2.0))
    entry, lines, notes = gate_time(timed, HOOKS, [SCAN], None)
    slowest = "The slowest check took 2.0 seconds: Sleep, then refuse FAIL."
    assert lines == (
        "Time of each check, in seconds:",
        "    1.2  pre-commit setup, such as installing hooks",
        "    0.1  check yaml",
        "    2.0  Sleep, then refuse FAIL",
        "    0.4  betterleaks history scan",
        f"The gate took 3.7 seconds. {slowest}",
    )
    assert entry == {
        "severity": "info",
        "category": "duration",
        "seconds": 3.7,
        "message": f"The gate took 3.7 seconds. {slowest}",
    }
    assert notes == ()


def test_a_skipped_hook_and_a_status_line_of_no_hook_are_no_checks():
    timed = pre_commit(
        5.0,
        (b"\x1b[32m" + YAML + b"\x1b[0m", 1.0),
        (SKIPPED, 1.5),
        (b"other hook...............................................Passed", 2.0),
        (b"\xff not text", 3.0),
    )
    _, lines, _ = gate_time(timed, HOOKS, [], None)
    assert lines == (
        "Time of each check, in seconds:",
        "    4.0  pre-commit setup, such as installing hooks",
        "    1.0  check yaml",
        "The gate took 5.0 seconds. The slowest check took 1.0 seconds: check yaml.",
    )


@pytest.mark.parametrize(
    "lines,scans,slowest",
    [
        (((YAML, 1.0), (SLEEP, 1.0)), [], "check yaml"),
        (((SLEEP, 0.4),), [SCAN], "Sleep, then refuse FAIL"),
        (((YAML, 0.1),), [SCAN], "betterleaks history scan"),
    ],
    ids=["tie of hooks", "tie of a hook and a scan", "scan"],
)
def test_the_first_of_the_slowest_checks_is_named(lines, scans, slowest):
    entry, _, _ = gate_time(pre_commit(9.0, *lines), HOOKS, scans, None)
    assert str(entry["message"]).endswith(f" seconds: {slowest}.")


def test_a_gate_without_a_check_lists_no_time():
    timed = pre_commit(3.3, (SKIPPED, 0.0))
    entry, lines, notes = gate_time(timed, HOOKS, [], None)
    assert entry["message"] == "The gate took 3.3 seconds."
    assert (lines, notes) == ((), ())


@pytest.mark.parametrize(
    "budget,elapsed,seconds",
    [(None, 42.34, 42.3), (300, 42.34, 42.3), (300, 300.04, 300.0)],
    ids=["no budget", "within", "at the budget once rounded"],
)
def test_a_gate_within_its_budget_does_not_warn(budget, elapsed, seconds):
    entry, _, notes = gate_time(pre_commit(elapsed, (YAML, 0.1)), HOOKS, [], budget)
    assert (entry["seconds"], entry.get("budget_seconds"), notes) == (
        seconds,
        budget,
        (),
    )


@pytest.mark.parametrize(
    "lines,warning",
    [
        (
            [(SLEEP, 250.1)],
            f"{OVER} The slowest check took 250.1 seconds: Sleep, then refuse FAIL.",
        ),
        ([(SKIPPED, 0.0)], OVER),
    ],
    ids=["a check ran", "no check ran"],
)
def test_a_gate_over_its_budget_warns_with_its_slowest_check(lines, warning):
    entry, _, notes = gate_time(pre_commit(312.4, *lines), HOOKS, [], 300)
    assert (entry["seconds"], entry["budget_seconds"]) == (312.4, 300)
    assert notes == (Note("warning", "over_time_budget", warning),)
