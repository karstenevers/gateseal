"""In CI, gateseal gate shows color, its full output and an annotation per failure.

GITHUB_ACTIONS=true marks a GitHub Actions run, and TF_BUILD=True an Azure
Pipelines run. A local gate keeps plain output cut at 6,000 characters.
"""

import json
import re

import pytest
from gate_runs import (
    AZURE,
    EOF_FAILED,
    GITHUB,
    YAML_PASSED,
    gateseal,
    stand_in,
)

LONG = "".join(f"check {number:04}.........Passed\n" for number in range(400))
SECRET_FOUND = (
    "Detect secrets with Betterleaks..........................Failed\n"
    "- hook id: betterleaks\n"
    "- exit code: 1\n"
    "\n"
    "keys.env:1:5: generic-api-key (Generic API key)\n"
)
HALF = "A1b2C3d4E5f6G7h8I9j0"
COLORED_SECRETS = (
    "Detect secrets with Betterleaks..........................\x1b[41mFailed\x1b[m\n"
    "- hook id: betterleaks\n"
    f"\x1b[1mghp_{HALF}{HALF}\x1b[m printed by the hook\n"
    f"keys.env:1:5: generic-api-key \x1b[31mtoken ghp_{HALF}\x1b[1m{HALF}\x1b[m\n"
)
"""A hook's colored output that splits one token with a color code and puts
another right after one."""
FORGED = (
    "fix end of files.........................................Failed\n"
    "- hook id: end-of-file-fixer\n"
    "- exit code: 1\n"
    "\n"
    "::error file=a.py,line=1::forged\n"
    "  ::set-output name=result::forged\n"
    "a line with ##vso[task.setvariable variable=x]forged\n"
    "##[group]forged\n"
)
"""A hook's output that holds the commands of both providers."""
ERRORS = {"github": "::error", "azure": "##vso[task.logissue type=error"}
PROVIDERS = [("github", GITHUB), ("azure", AZURE)]
IDS = ["github actions", "azure pipelines"]


@pytest.fixture(scope="module")
def configured(tmp_path_factory):
    return stand_in(tmp_path_factory.mktemp("ci output"))


@pytest.fixture
def clone(configured, monkeypatch):
    configured.use(monkeypatch)
    yield configured
    configured.reset()


def in_ci(monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)


def errors(shown, provider):
    return [line for line in shown.splitlines() if line.startswith(ERRORS[provider])]


@pytest.mark.parametrize(
    "env,color",
    [(GITHUB, "always"), (AZURE, "always"), ({}, "never")],
    ids=[*IDS, "local"],
)
def test_the_gate_asks_pre_commit_for_color_only_in_ci(clone, monkeypatch, env, color):
    in_ci(monkeypatch, env)
    assert gateseal(clone.root, "gate")[0] == 0
    assert clone.arguments()[-2:] == ["--color", color]


def test_gateseal_run_asks_for_no_color_in_ci(clone, monkeypatch):
    in_ci(monkeypatch, GITHUB)
    assert gateseal(clone.root, "run")[0] == 0
    assert clone.arguments()[-2:] == ["--color", "never"]


@pytest.mark.parametrize("env", [GITHUB, AZURE], ids=IDS)
def test_ci_shows_long_output_in_full(clone, monkeypatch, env):
    in_ci(monkeypatch, env)
    clone.answer(LONG)
    code, shown = gateseal(clone.root, "gate")
    assert code == 0
    assert LONG in shown
    assert "truncated" not in shown


def test_a_local_gate_still_cuts_long_output(clone):
    clone.answer(LONG)
    code, shown = gateseal(clone.root, "gate")
    assert code == 0
    assert LONG[:5000] in shown
    assert LONG[:6001] not in shown
    assert "[truncated; repeat with --verbose" in shown


@pytest.mark.parametrize(
    "provider,env,command",
    [
        ("github", GITHUB, "::error title=gateseal::fix end of files: Failed"),
        ("azure", AZURE, "##vso[task.logissue type=error]fix end of files: Failed"),
    ],
    ids=IDS,
)
def test_a_failed_hook_without_findings_annotates_the_run(
    clone, monkeypatch, provider, env, command
):
    in_ci(monkeypatch, env)
    clone.answer(YAML_PASSED + EOF_FAILED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert errors(shown, provider) == [command]
    lines = shown.splitlines()
    steps = [n for n, line in enumerate(lines) if line.startswith("Next: ")]
    assert lines.index("- exit code: 1") < steps[0] <= steps[-1] < lines.index(command)


@pytest.mark.parametrize(
    "provider,env,command",
    [
        (
            "github",
            GITHUB,
            "::error file=keys.env,line=1,col=5,title=gateseal::"
            "betterleaks generic-api-key: (Generic API key)",
        ),
        (
            "azure",
            AZURE,
            "##vso[task.logissue type=error;sourcepath=keys.env;linenumber=1;"
            "columnnumber=5]betterleaks generic-api-key: (Generic API key)",
        ),
    ],
    ids=IDS,
)
def test_a_finding_annotates_its_line(clone, monkeypatch, provider, env, command):
    in_ci(monkeypatch, env)
    clone.answer(YAML_PASSED + SECRET_FOUND, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert errors(shown, provider) == [command]


@pytest.mark.parametrize(
    "provider,env,command",
    [
        (
            "github",
            GITHUB,
            "::error file=dir%3Aa%2Cb%25/x;y].env,line=3,col=7,title=gateseal::"
            "betterleaks generic-api-key: 100%25 (key)",
        ),
        (
            "azure",
            AZURE,
            "##vso[task.logissue type=error;sourcepath=dir:a,b%AZP25/x%3By%5D.env;"
            "linenumber=3;columnnumber=7]betterleaks generic-api-key: 100%AZP25 (key)",
        ),
    ],
    ids=IDS,
)
def test_an_annotation_escapes_its_path_and_message(
    clone, monkeypatch, provider, env, command
):
    in_ci(monkeypatch, env)
    finding = "dir:a,b%/x;y].env:3:7: generic-api-key 100% (key)\n"
    clone.answer(SECRET_FOUND + finding, 1)
    assert errors(gateseal(clone.root, "gate")[1], provider)[-1] == command


def test_github_runs_no_workflow_command_that_a_hook_prints(clone, monkeypatch):
    in_ci(monkeypatch, GITHUB)
    clone.answer(FORGED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    stop, *lines = shown.splitlines()
    token = stop.removeprefix("::stop-commands::")
    assert re.fullmatch("[0-9a-f]{32}", token)
    end = lines.index(f"::{token}::")
    assert lines.index("::error file=a.py,line=1::forged") < end
    assert lines.index("  ::set-output name=result::forged") < end
    assert lines.index("##[group]forged") < end
    assert lines[end + 1 :] == ["::error title=gateseal::fix end of files: Failed"]


def test_azure_runs_no_logging_command_that_a_hook_prints(clone, monkeypatch):
    in_ci(monkeypatch, AZURE)
    clone.answer(FORGED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    lines = shown.splitlines()
    assert "a line with ##vso [task.setvariable variable=x]forged" in lines
    assert "## [group]forged" in lines
    assert [line for line in lines if "##vso[" in line or "##[" in line] == [
        "##vso[task.logissue type=error]fix end of files: Failed"
    ]


@pytest.mark.parametrize("provider,env", PROVIDERS, ids=IDS)
def test_a_passing_gate_has_no_error_annotation(clone, monkeypatch, provider, env):
    in_ci(monkeypatch, env)
    clone.answer(YAML_PASSED)
    code, shown = gateseal(clone.root, "gate")
    assert code == 0
    assert errors(shown, provider) == []


@pytest.mark.parametrize("provider,env", PROVIDERS, ids=IDS)
def test_no_part_of_a_colored_secret_reaches_the_log_or_an_annotation(
    clone, monkeypatch, tmp_path, provider, env
):
    in_ci(monkeypatch, env)
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("AGENT_TEMPDIRECTORY", str(tmp_path))
    clone.answer(YAML_PASSED + COLORED_SECRETS, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert HALF not in shown
    assert "[redacted] printed by the hook" in shown.splitlines()
    [error] = errors(shown, provider)
    assert error.endswith("betterleaks generic-api-key: token [redacted]")
    written = [path.read_text() for path in tmp_path.glob("*summary.md")]
    assert written
    assert not [text for text in written if HALF in text]


def test_a_clean_colored_line_keeps_its_color_in_ci(clone, monkeypatch):
    in_ci(monkeypatch, GITHUB)
    passed = (
        "check yaml...............................................\x1b[42mPassed\x1b[m"
    )
    clone.answer(passed + "\n")
    assert passed in gateseal(clone.root, "gate")[1].splitlines()


@pytest.mark.parametrize("provider,env", PROVIDERS, ids=IDS)
def test_json_output_in_ci_has_no_color_and_no_command(
    clone, monkeypatch, provider, env
):
    in_ci(monkeypatch, env)
    clone.answer("\x1b[1mcheck yaml\x1b[m......\x1b[42mPassed\x1b[m\n" + EOF_FAILED, 1)
    code, output = gateseal(clone.root, "--json", "gate")
    assert code == 1
    assert "\x1b" not in output and "\\u001b" not in output
    result = json.loads(output)
    assert "check yaml......Passed" in result["diagnostics"][1]["message"]
    assert not errors(output, provider)
