import re
from importlib.resources import files
from pathlib import Path

import pytest
from conftest import PIN
from gate_files import BRANCH, SERVED, gate_file
from gateseal.ci_providers import PROVIDERS
from gateseal.ci_providers.github import CHECKOUT
from gateseal.diagnostics import GatesealError
from gateseal.lock import compile_outputs
from gateseal.manifest import CI as Table
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from gateseal.tools import load_definition
from gateseal.tools.model import HOOK_STAGES, Origin, Stage

POLICY = load_bundled_policy()
EXPECTED = Path(__file__).with_name("expected_github_workflow.yml")


def bundled(extra=""):
    """Return a Manifest without [ci], or with the text of an invalid table."""
    return render(("hygiene", "secrets"), BundledSource(POLICY.release)) + (
        extra.encode()
    )


def test_the_workflow_equals_the_reviewed_file():
    """A change of these bytes makes every repository run gateseal configure."""
    assert gate_file("github", SERVED["github"]).data == EXPECTED.read_bytes()


def test_the_workflow_guards_every_event_with_read_only_full_history():
    document = gate_file("github").document
    job = document["jobs"]["gateseal"]
    events = document[True]
    assert set(events) == {
        "push",
        "pull_request",
        "merge_group",
        "workflow_dispatch",
        "schedule",
    }
    assert events["push"] == {"branches": [BRANCH]}
    assert all(
        value is None
        for name, value in events.items()
        if name not in ("push", "schedule")
    )
    assert "paths" not in str(events)
    assert document["permissions"] == {"contents": "read"}
    assert set(document["jobs"]) == {"gateseal"}
    assert job["name"] == (
        "${{ github.event_name == 'push' && 'gateseal-push' || 'gateseal' }}"
    )
    checkout, prepare, gate = job["steps"]
    assert checkout["uses"] == CHECKOUT
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    assert gate["env"]["GATESEAL_HEAD"] == "${{ github.sha }}"
    assert prepare["run"].splitlines()[-2:] == [
        '"$command/bin/gateseal" setup',
        '"$command/bin/gateseal" check',
    ]
    assert (
        gate["run"]
        .rstrip()
        .endswith('"$RUNNER_TEMP/gateseal-command/bin/gateseal" gate "${scope[@]}"')
    )
    assert "TOKEN" not in str(gate["env"])


def evaluate(text, github):
    """Evaluate each ${{ }} in text against the github context, as GitHub does.

    Only the syntax the concurrency mapping needs: a value that is one
    expression keeps its type, and an expression inside a string becomes text.
    """
    whole = re.fullmatch(r"\$\{\{ (.*) \}\}", text)
    if whole:
        return expression(whole[1], github)
    return re.sub(r"\$\{\{ (.*?) \}\}", lambda m: str(expression(m[1], github)), text)


def expression(text, github):
    values = [comparison(part, github) for part in text.split(" || ")]
    return next((value for value in values if value), values[-1])


def comparison(text, github):
    left, *right = text.split(" == ")
    value = operand(left, github)
    return value == operand(right[0], github) if right else value


def operand(text, github):
    if text.startswith("'"):
        return text.strip("'")
    value = {"github": github}
    for key in text.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value


def run_concurrency(event, run_id, pull_request=None):
    """Return the concurrency group and cancel flag of one run of the workflow."""
    github = {"event_name": event, "run_id": run_id, "event": {}}
    if pull_request is not None:
        github["event"]["pull_request"] = {"number": pull_request}
    concurrency = gate_file("github").document["concurrency"]
    return (
        evaluate(concurrency["group"], github),
        evaluate(concurrency["cancel-in-progress"], github),
    )


OTHER_EVENTS = ("push", "merge_group", "workflow_dispatch", "schedule")


def test_a_new_run_of_a_pull_request_cancels_its_older_run():
    older = run_concurrency("pull_request", 19000000001, pull_request=7)
    newer = run_concurrency("pull_request", 19000000002, pull_request=7)
    assert older[0] == newer[0]
    assert older[1] is True and newer[1] is True


def test_runs_of_different_pull_requests_never_cancel_each_other():
    first = run_concurrency("pull_request", 19000000001, pull_request=7)
    second = run_concurrency("pull_request", 19000000002, pull_request=8)
    assert first[0] != second[0]


@pytest.mark.parametrize("event", OTHER_EVENTS)
def test_every_other_run_has_a_group_of_its_own_and_never_cancels(event):
    first = run_concurrency(event, 7)
    second = run_concurrency(event, 8)
    pull_requests = {
        run_concurrency("pull_request", run_id, pull_request=7)[0] for run_id in (7, 8)
    }
    assert first[0] != second[0]
    assert first[1] is False and second[1] is False
    assert not {first[0], second[0]} & pull_requests


def test_a_manifest_without_ci_has_no_workflow():
    selection = resolve(parse_manifest(bundled()), POLICY)
    outputs = compile_outputs(selection, PIN, BundledSource(POLICY.release))
    assert PROVIDERS["github"].path not in outputs


@pytest.mark.parametrize(
    "table,code",
    [
        ('provider = "gitlab"\ndefault_branch = "main"', "unsupported_ci_provider"),
        ('provider = "github"', "invalid_field"),
        ('provider = "github"\ndefault_branch = "main"\nextra = 1', "invalid_field"),
        *(
            (f'provider = "github"\ndefault_branch = "{branch}"', "invalid_field")
            for branch in (
                "",
                "a..b",
                "/main",
                "main/",
                ".main",
                "main.",
                "main.lock",
                "a//b",
                "main branch",
                "main$x",
            )
        ),
    ],
)
def test_ci_names_a_known_provider_and_a_branch(table, code):
    with pytest.raises(GatesealError) as failure:
        parse_manifest(bundled(f"\n[ci]\n{table}\n"))
    assert failure.value.code == code


def test_render_writes_the_ci_table_configure_creates():
    text = render(("hygiene",), BundledSource(POLICY.release), Table("github", "main"))
    assert '\n[ci]\nprovider = "github"\ndefault_branch = "main"\n' in text.decode()
    assert parse_manifest(text).ci == Table("github", "main")


def test_each_stage_runs_in_a_pre_commit_stage():
    assert set(HOOK_STAGES) == set(Stage)


def definition(name):
    return files("gateseal").joinpath("data", "tools", f"{name}.tool.toml").read_text()


HISTORY = '\n[history]\nargs = ["dir", "--config", "{configuration}", "{range}"]\n'
RANGE_ARGUMENT = (
    '"--log-opts=--diff-merges=first-parent --text --no-textconv --no-ext-diff '
    '--no-color {range}",\n]'
)


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("ruff", "", HISTORY),
        (
            "betterleaks",
            '[history]\nargs = [\n  "git",',
            '[history]\nargs = [\n  "{x}",',
        ),
        ("betterleaks", RANGE_ARGUMENT, RANGE_ARGUMENT + '\nshell = "sh"'),
        ("betterleaks", RANGE_ARGUMENT, '"--log-opts",\n]'),
        ("betterleaks", RANGE_ARGUMENT, '"{range}", "{range}",\n]'),
    ],
    ids=[
        "history of a hook repository",
        "unknown placeholder",
        "unknown field",
        "no range",
        "two ranges",
    ],
)
def test_only_a_local_tool_scans_history_with_owned_arguments(name, old, new):
    text = definition(name)
    changed = text + new if not old else text.replace(old, new, 1)
    assert changed != text
    with pytest.raises(GatesealError) as failure:
        load_definition(name, changed.encode(), Origin.BUILT_IN)
    assert failure.value.code == "invalid_field"


def test_an_empty_history_scan_is_refused():
    text = definition("betterleaks")
    start = text.index("[history]")
    with pytest.raises(GatesealError) as failure:
        load_definition(
            "betterleaks",
            (text[:start] + "[history]\nargs = []\n").encode(),
            Origin.BUILT_IN,
        )
    assert failure.value.code == "invalid_field"
