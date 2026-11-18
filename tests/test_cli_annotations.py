"""In CI, text output prints each warning as the provider's own command.

GitHub shows a ::warning command, and Azure Pipelines a task.logissue command,
as an annotation of the run. A consumer whose
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
    "variable,value,prefix,percent",
    [
        ("GITHUB_ACTIONS", "true", "::warning title=gateseal::", "100%25"),
        ("GITHUB_ACTIONS", "false", "Warning: ", "100%"),
        ("TF_BUILD", "True", "##vso[task.logissue type=warning]", "100%AZP25"),
        ("TF_BUILD", "true", "Warning: ", "100%"),
        (None, None, "Warning: ", "100%"),
    ],
    ids=[
        "github actions",
        "not github actions",
        "azure pipelines",
        "not azure",
        "unset",
    ],
)
def test_a_warning_is_the_command_of_the_ci_provider_that_runs_it(
    excepted, monkeypatch, variable, value, prefix, percent
):
    for name in ("GITHUB_ACTIONS", "TF_BUILD"):
        monkeypatch.delenv(name, raising=False)
    if variable is not None:
        monkeypatch.setenv(variable, value)
    [warning] = [
        line for line in gateseal(excepted, "check").splitlines() if MESSAGE in line
    ]
    assert warning.startswith(prefix + MESSAGE)
    assert f"{percent} of hooks" in warning


@pytest.mark.parametrize(
    "variable,value", [("GITHUB_ACTIONS", "true"), ("TF_BUILD", "True")]
)
def test_json_output_has_no_command(excepted, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    output = gateseal(excepted, "--json", "check")
    [warning] = [
        d for d in json.loads(output)["diagnostics"] if d["severity"] == "warning"
    ]
    assert warning["message"].startswith(MESSAGE)
    assert "::warning" not in output and "##vso" not in output
