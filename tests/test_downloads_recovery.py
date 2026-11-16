"""A failed download names the step that fits the cause uv reports.

Each test raises the failure that execute raises for uv, with uv's own output
as its message, inside the block that activation wraps around the download.
"""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.downloads import python_download

PYTHON = "https://example.invalid/cpython-3.14.0-linux.tar.gz"
UNREACHABLE = "error: Request failed\n  Caused by: error sending request for url (x)"
MISMATCH = "error: Hash mismatch for `cpython-3.14.0`"


def recovered(context, output):
    with pytest.raises(GatesealError) as failure, context:
        raise GatesealError("tool_failed", output, 4, "gateseal activate")
    return failure.value


@pytest.mark.parametrize(
    "output,named",
    [
        (UNREACHABLE, (PYTHON, "network", "gateseal activate")),
        (MISMATCH, ("/cache/python", "SHA-256", "gateseal activate")),
    ],
    ids=["unreachable", "hash mismatch"],
)
def test_a_python_download_names_the_step_for_its_cause(output, named):
    error = recovered(python_download(PYTHON, "/cache/python"), output)
    assert (error.code, str(error), error.exit_code) == ("tool_failed", output, 4)
    assert all(value in error.next_step for value in named)


def test_a_failure_with_no_known_cause_keeps_its_own_step():
    context = python_download(PYTHON, "/cache")
    error = recovered(context, "error: No space left on device")
    assert error.next_step == "gateseal activate"


def test_a_download_that_succeeds_raises_nothing():
    with python_download(PYTHON, "/cache"):
        pass
