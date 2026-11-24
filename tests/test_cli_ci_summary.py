"""In CI, a failed gate publishes a Markdown summary of the run.

GitHub Actions appends it to the file that GITHUB_STEP_SUMMARY names. Azure
Pipelines gets it as a file in AGENT_TEMPDIRECTORY and a task.uploadsummary
command. A passing gate and JSON output publish nothing.
"""

import json

import pytest
from gate_runs import AZURE, EOF_FAILED, GITHUB, YAML_PASSED, gateseal, stand_in
from gateseal.ci_providers import PROVIDERS
from gateseal.cli import main

SUMMARY = """\
### gateseal gate

Checks failed or modified files. The gate checks every file and scans no history.

Failed checks:

- fix end of files: Failed

Next steps:

- Read the output of each failed hook, fix what it reports, then push again.
"""
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" * 2


@pytest.fixture(scope="module")
def configured(tmp_path_factory):
    return stand_in(tmp_path_factory.mktemp("ci summary"))


@pytest.fixture
def clone(configured, monkeypatch):
    configured.use(monkeypatch)
    yield configured
    configured.reset()


def in_ci(monkeypatch, **env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def test_github_appends_the_summary_to_the_step_summary_file(
    clone, monkeypatch, tmp_path
):
    summary = tmp_path / "step summary.md"
    summary.write_text("earlier step\n")
    in_ci(monkeypatch, **GITHUB, GITHUB_STEP_SUMMARY=str(summary))
    clone.answer(YAML_PASSED + EOF_FAILED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert summary.read_text() == "earlier step\n" + SUMMARY
    assert shown.splitlines()[-1] == (
        "::error title=gateseal::fix end of files: Failed"
    )


def test_azure_writes_the_summary_and_uploads_it(clone, monkeypatch, tmp_path):
    agent = tmp_path / "agent 100%"
    agent.mkdir()
    written = agent / "gateseal-summary.md"
    written.write_text("an earlier gate\n")
    in_ci(monkeypatch, **AZURE, AGENT_TEMPDIRECTORY=str(agent))
    clone.answer(YAML_PASSED + EOF_FAILED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert written.read_text() == SUMMARY
    escaped = str(written).replace("%", "%AZP25")
    assert shown.splitlines()[-1] == f"##vso[task.uploadsummary]{escaped}"


@pytest.mark.parametrize(
    "env,last",
    [
        (GITHUB, "::error title=gateseal::fix end of files: Failed"),
        (AZURE, "##vso[task.logissue type=error]fix end of files: Failed"),
    ],
    ids=["github", "azure"],
)
def test_without_its_variable_a_provider_publishes_nothing(
    clone, monkeypatch, env, last
):
    in_ci(monkeypatch, **env)
    clone.answer(EOF_FAILED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert shown.splitlines()[-1] == last


@pytest.mark.parametrize("provider", ["github", "azure"])
def test_a_provider_gives_no_line_to_print_without_its_variable(provider):
    assert PROVIDERS[provider].publish_summary(SUMMARY, {}) == ""


def test_github_gives_no_line_to_print_after_it_writes(tmp_path):
    summary = tmp_path / "summary.md"
    assert (
        PROVIDERS["github"].publish_summary(
            "x\n", {"GITHUB_STEP_SUMMARY": str(summary)}
        )
        == ""
    )
    assert summary.read_text() == "x\n"


@pytest.mark.parametrize(
    "arguments,answer,code",
    [(["gate"], YAML_PASSED, 0), (["--json", "gate"], EOF_FAILED, 1)],
    ids=["passing gate", "json output"],
)
def test_a_passing_gate_and_json_output_publish_no_summary(
    clone, monkeypatch, tmp_path, arguments, answer, code
):
    summary = tmp_path / "summary.md"
    summary.write_text("earlier step\n")
    in_ci(monkeypatch, **GITHUB, GITHUB_STEP_SUMMARY=str(summary))
    clone.answer(answer, code)
    found, shown = gateseal(clone.root, *arguments)
    assert found == code
    assert summary.read_text() == "earlier step\n"
    monkeypatch.delenv("GITHUB_ACTIONS")
    in_ci(monkeypatch, **AZURE, AGENT_TEMPDIRECTORY=str(tmp_path))
    found, shown = gateseal(clone.root, *arguments)
    assert found == code
    assert not (tmp_path / "gateseal-summary.md").exists()
    assert "##vso" not in shown


def test_json_output_in_ci_is_one_json_document(clone, monkeypatch, tmp_path):
    in_ci(monkeypatch, **AZURE, AGENT_TEMPDIRECTORY=str(tmp_path))
    clone.answer(EOF_FAILED, 1)
    code, shown = gateseal(clone.root, "--json", "gate")
    assert code == 1
    assert json.loads(shown)["code"] == "checks_failed"


@pytest.mark.parametrize(
    "env,variable,command,problem",
    [
        (
            GITHUB,
            "GITHUB_STEP_SUMMARY",
            "::warning title=gateseal::",
            "[Errno 21] Is a directory: '{}'",
        ),
        (
            AZURE,
            "AGENT_TEMPDIRECTORY",
            "##vso[task.logissue type=warning]",
            "[Errno 20] Not a directory: '{}/gateseal-summary.md'",
        ),
    ],
    ids=["github", "azure"],
)
def test_a_summary_that_cannot_be_written_warns_and_keeps_the_exit_code(
    clone, monkeypatch, tmp_path, env, variable, command, problem
):
    blocked = tmp_path / TOKEN
    if variable == "GITHUB_STEP_SUMMARY":
        blocked.mkdir()
    else:
        blocked.write_text("a file, not a directory\n")
    in_ci(monkeypatch, **env, **{variable: str(blocked)})
    clone.answer(EOF_FAILED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    redacted = problem.format(tmp_path / "[redacted]")
    assert shown.splitlines()[-1] == (
        f"{command}gateseal could not write the run summary: {redacted}"
    )


@pytest.mark.parametrize(
    "arguments,heading",
    [
        (["--repo", "check", "run", "--bogus"], "### gateseal run"),
        (["bogus"], "### gateseal"),
    ],
    ids=["a value that names a command", "an unknown command"],
)
def test_the_summary_of_an_invalid_invocation_names_its_command(
    monkeypatch, tmp_path, arguments, heading
):
    summary = tmp_path / "summary.md"
    in_ci(monkeypatch, **GITHUB, GITHUB_STEP_SUMMARY=str(summary))
    assert main(arguments) == 2
    assert summary.read_text().splitlines()[0] == heading
