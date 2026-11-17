"""Start exact executables, find the documented tools, and ask uv for a Python."""

import os

import pytest
from gateseal.diagnostics import NEXT_STEPS, GatesealError
from gateseal.processes import execute, find_tool

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
