"""Start exact executables, find the documented tools, and ask uv for a Python."""

import os
import signal
import time

import pytest
from gateseal.diagnostics import NEXT_STEPS, GatesealError
from gateseal.processes import execute, execute_timed, find_tool, timed_lines

SPEAKS = ["sh", "-c", "echo out; echo err >&2; exit 3"]


def refused(call, *arguments, **options):
    with pytest.raises(GatesealError) as failure:
        call(*arguments, **options)
    return failure.value


def test_a_failed_tool_reports_both_streams_and_the_step_to_retry(tmp_path):
    error = refused(execute, SPEAKS, tmp_path, dict(os.environ))
    assert (error.code, str(error), error.exit_code) == ("tool_failed", "out\nerr\n", 4)
    assert error.next_step == NEXT_STEPS["tool_failed"]
    named = refused(
        execute, SPEAKS, tmp_path, dict(os.environ), next_step="gateseal configure"
    )
    assert named.next_step == "gateseal configure"


def test_an_allowed_failure_returns_the_process(tmp_path):
    result = execute(SPEAKS, tmp_path, dict(os.environ), allow_failure=True)
    assert (result.returncode, result.stdout, result.stderr) == (3, b"out\n", b"err\n")


def test_an_executable_that_cannot_start_is_unavailable(tmp_path):
    error = refused(execute, [str(tmp_path / "missing")], tmp_path, {})
    assert (error.code, error.exit_code) == ("tool_unavailable", 4)


def test_a_missing_tool_names_its_prerequisite(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path))
    error = refused(find_tool, "uv")
    assert (error.code, error.exit_code) == ("missing_prerequisite", 4)
    assert "uv" in str(error).split()


@pytest.mark.parametrize(
    "chunks,lines",
    [
        ([(1.0, b"one\ntwo\n")], [(b"one", 0.0), (b"two", 0.0)]),
        (
            [(1.0, b"na"), (2.5, b"me."), (4.0, b"..Passed\n")],
            [(b"name...Passed", 3.0)],
        ),
        (
            [(1.0, b"a\nb"), (3.0, b"c\nd"), (6.0, b"\n")],
            [(b"a", 0.0), (b"bc", 2.0), (b"d", 3.0)],
        ),
        ([(1.0, b"x\n"), (2.0, b"y\n")], [(b"x", 0.0), (b"y", 0.0)]),
        (
            [(1.0, b"x"), (2.0, b"\n\n"), (3.0, b"\n")],
            [(b"x", 1.0), (b"", 0.0), (b"", 0.0)],
        ),
        ([(1.0, b"x\n"), (2.0, b"no line feed")], [(b"x", 0.0)]),
    ],
    ids=[
        "one chunk",
        "across chunks",
        "several",
        "chunk ends a line",
        "empty",
        "last piece",
    ],
)
def test_a_line_takes_from_its_first_byte_to_its_line_feed(chunks, lines):
    assert timed_lines(chunks) == tuple(lines)


def test_a_timed_process_returns_both_streams_and_the_time_of_each_line(tmp_path):
    script = "printf start; sleep 0.3; echo ' end'; echo err >&2; exit 3"
    before = time.monotonic()
    timed = execute_timed(["sh", "-c", script], tmp_path, dict(os.environ))
    took = time.monotonic() - before
    process = timed.process
    assert (process.returncode, process.stdout, process.stderr) == (
        3,
        b"start end\n",
        b"err\n",
    )
    [(line, seconds)] = timed.lines
    assert line == b"start end"
    assert 0.3 <= seconds <= timed.seconds <= took


def test_a_timed_executable_that_cannot_start_is_unavailable(tmp_path):
    error = refused(execute_timed, [str(tmp_path / "missing")], tmp_path, {})
    assert (error.code, error.exit_code) == ("tool_unavailable", 4)


class Interrupted(Exception):
    pass


def interrupt(signum, frame):
    raise Interrupted


def test_an_interrupted_timed_process_stops_at_once(tmp_path):
    script = "sleep 5 >/dev/null & echo started; exec sleep 30"
    previous = signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 0.5)
    before = time.monotonic()
    try:
        with pytest.raises(Interrupted):
            execute_timed(["sh", "-c", script], tmp_path, dict(os.environ))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert time.monotonic() - before < 3
