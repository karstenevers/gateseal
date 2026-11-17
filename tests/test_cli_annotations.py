"""In GitHub Actions, text output prints each warning as a workflow command.

GitHub shows a ::warning command as an annotation of the run. A consumer whose
Version Exception selects the policy's pin gets a redundant_exception warning
from gateseal check, through main in-process.
"""

import contextlib
import io
import json

import pytest
from gateseal.cli import main
from installation import clone

EXCEPTION = """
[exceptions.pre-commit-hooks]
version = "6.0.0"
reason = "Kept for 100% of hooks"
review_by = 2099-01-01
"""
MESSAGE = "Version exception pre-commit-hooks 6.0.0"


def gateseal(root, *arguments):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), *arguments])
    assert code == 0, output.getvalue()
    return output.getvalue()


@pytest.fixture(scope="module")
def excepted(tmp_path_factory):
    root = tmp_path_factory.mktemp("annotated")
    consumer = clone(root / "origin", root / "consumer")
    gateseal(consumer, "configure", "--capability", "hygiene")
    manifest = consumer / "gateseal.toml"
    manifest.write_text(manifest.read_text() + EXCEPTION)
    gateseal(consumer, "configure")
    return consumer


@pytest.mark.parametrize(
    "value,prefix",
    [
        ("true", "::warning title=gateseal::"),
        ("false", "Warning: "),
        (None, "Warning: "),
    ],
    ids=["github actions", "false", "unset"],
)
def test_a_warning_is_a_workflow_command_only_in_github_actions(
    excepted, monkeypatch, value, prefix
):
    if value is None:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", value)
    [warning] = [
        line for line in gateseal(excepted, "check").splitlines() if MESSAGE in line
    ]
    assert warning.startswith(prefix + MESSAGE)
    assert ("100%25" in warning) == (value == "true")


def test_json_output_has_no_workflow_command(excepted, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    output = gateseal(excepted, "--json", "check")
    [warning] = [
        d for d in json.loads(output)["diagnostics"] if d["severity"] == "warning"
    ]
    assert warning["message"].startswith(MESSAGE)
    assert "::warning" not in output
