"""Start exact executables, find the documented tools, and ask uv for a Python."""

import os
import sys
from pathlib import Path

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.environments import uv_environment
from gateseal.processes import execute, find_python, find_tool

SPEAKS = ["sh", "-c", "echo out; echo err >&2; exit 3"]


def refused(call, *arguments, **options):
    with pytest.raises(GatesealError) as failure:
        call(*arguments, **options)
    return failure.value


def test_a_failed_tool_reports_both_streams_and_the_step_to_retry(tmp_path):
    error = refused(execute, SPEAKS, tmp_path, dict(os.environ))
    assert (error.code, str(error), error.exit_code) == ("tool_failed", "out\nerr\n", 4)
    assert error.next_step == "gateseal activate"
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


def test_uv_finds_the_python_it_was_asked_for(tmp_path):
    uv = find_tool("uv")
    found = find_python(uv, tmp_path, uv_environment(), sys.executable)
    assert Path(found).is_absolute()
    assert Path(found).samefile(sys.executable)


@pytest.mark.parametrize("answer", ["bin/python3", "/missing/bin/python3"])
def test_an_answer_that_is_no_python_executable_is_refused(tmp_path, answer):
    """A stand-in uv answers what a broken uv could; the real one never does."""
    uv = tmp_path / "uv"
    uv.write_text(f"#!/bin/sh\necho {answer}\n")
    uv.chmod(0o755)
    error = refused(find_python, str(uv), tmp_path, dict(os.environ), "3.14")
    assert (error.code, error.exit_code) == ("invalid_preparation", 4)
    assert "uv" in str(error).split()
