"""The optional [ci] keys reach each provider's file in its own syntax.

Each test renders the file for a Manifest and reads it as YAML.
"""

import contextlib
import io
import json
import tomllib

import pytest
import yaml
from gate_files import BRANCH, SERVED, gate_file, job, machine, pushes, timeout, weekly
from gateseal.ci_providers import PROVIDERS
from gateseal.ci_providers.model import GENERATED
from gateseal.cli import main
from installation import clone

EACH = pytest.mark.parametrize("provider", sorted(PROVIDERS))

GITHUB_EVENTS = {"push", "pull_request", "merge_group", "workflow_dispatch"}
JOB_NAME = "${{ github.event_name == 'push' && 'gateseal-push' || 'gateseal' }}"
HOSTED = {"azure": {"vmImage": "ubuntu-24.04"}, "github": "ubuntu-24.04"}
WEEKLY = {
    "azure": [
        {
            "cron": "37 4 * * 1",
            "displayName": "Weekly gateseal gate",
            "branches": {"include": [BRANCH]},
            "always": True,
        }
    ],
    "github": [{"cron": "37 4 * * 1"}],
}
RUNNER = {
    "azure": (
        'pool = "linux \\"agents\\": x64"\n',
        {
            "name": 'linux "agents": x64',
            "demands": ["Agent.OS -equals Linux", "Agent.OSArchitecture -equals X64"],
        },
    ),
    "github": (
        'runs_on = ["self-hosted", "linux", "x64: \\"big\\""]\n',
        ["self-hosted", "linux", 'x64: "big"'],
    ),
}
"""The runner key of each provider, and the machine its file then names."""


def manual(provider, file):
    """Whether someone can start the pipeline by hand, and the gate checks it.

    Azure Pipelines lets anyone with access start any pipeline.
    """
    if provider == "github":
        return "workflow_dispatch" in file.document[True]
    return "Manual) " in file.gate.script


@EACH
def test_without_the_new_keys_the_file_keeps_its_triggers_image_and_timeout(provider):
    file = gate_file(provider)
    document = file.document
    assert pushes(provider, document) == [BRANCH]
    assert weekly(provider, document) is None
    assert manual(provider, file)
    assert timeout(provider, document) == 30
    assert machine(provider, document) == HOSTED[provider]
    if provider == "github":
        assert set(document[True]) == GITHUB_EVENTS
        assert job(provider, document)["name"] == JOB_NAME
    else:
        assert set(document) == {"trigger", "jobs"}
        assert job(provider, document)["job"] == "gateseal"


@EACH
def test_branches_join_the_push_trigger_in_the_providers_syntax(provider):
    keys = 'branches = ["release/*", "team/release/*", "develop"]\n'
    written = {
        "azure": ["release/*", "team/release/*", "develop"],
        "github": ["release/**", "team/release/**", "develop"],
    }
    document = gate_file(provider, keys=keys).document
    assert pushes(provider, document) == [BRANCH, *written[provider]]


@EACH
def test_an_empty_branches_list_checks_the_default_branch_only(provider):
    assert gate_file(provider, keys="branches = []\n").data == gate_file(provider).data


@EACH
def test_schedule_true_adds_the_weekly_run_of_the_default_branch(provider):
    keys = 'branches = ["release/*"]\nschedule = true\n'
    file = gate_file(provider, keys=keys)
    assert weekly(provider, file.document) == WEEKLY[provider]
    assert manual(provider, file)


@EACH
@pytest.mark.parametrize("keys", ["", "schedule = false\n"], ids=["absent", "false"])
def test_without_schedule_true_only_a_manual_run_checks_without_a_change(
    provider, keys
):
    file = gate_file(provider, keys=keys)
    assert weekly(provider, file.document) is None
    assert manual(provider, file)
    assert file.data == gate_file(provider).data


@EACH
@pytest.mark.parametrize("minutes", [1, 60, 360])
def test_timeout_minutes_sets_the_jobs_time_limit(provider, minutes):
    document = gate_file(provider, keys=f"timeout_minutes = {minutes}\n").document
    assert timeout(provider, document) == minutes


@EACH
def test_the_runner_key_names_the_teams_machine(provider):
    keys, named = RUNNER[provider]
    assert machine(provider, gate_file(provider, keys=keys).document) == named


@EACH
def test_standalone_true_is_the_pipeline_of_its_own(provider):
    explicit = gate_file(provider, keys="standalone = true\n")
    assert explicit.data == gate_file(provider).data


@EACH
def test_standalone_false_writes_the_same_job_without_triggers(provider):
    keys = f"timeout_minutes = 45\n{RUNNER[provider][0]}"
    own = gate_file(provider, SERVED[provider], keys=keys).document
    included = gate_file(
        provider, SERVED[provider], branch=None, keys=f"standalone = false\n{keys}"
    ).document
    assert included["jobs"] == own["jobs"]
    assert timeout(provider, included) == 45
    assert machine(provider, included) == RUNNER[provider][1]
    assert (
        set(included)
        == {
            "azure": {"jobs"},
            "github": {"name", True, "permissions", "jobs"},
        }[provider]
    )


def test_a_called_workflow_starts_only_from_its_caller_and_takes_the_token():
    document = gate_file("github", branch=None, keys="standalone = false\n").document
    assert document[True] == {
        "workflow_call": {"secrets": {"GATESEAL_POLICY_TOKEN": {"required": False}}}
    }
    assert "concurrency" not in document
    assert document["permissions"] == {"contents": "read"}
    assert job("github", document)["name"] == JOB_NAME


def test_an_included_pipeline_file_holds_only_the_job():
    file = gate_file("azure", SERVED["azure"], branch=None, keys="standalone = false\n")
    assert file.data.startswith(f"{GENERATED}\njobs:\n  - job: gateseal\n".encode())
    assert set(file.document) == {"jobs"}
    assert job("azure", file.document)["uses"] == {"repositories": ["gateseal_policy"]}


@EACH
def test_configure_writes_a_ci_table_without_the_optional_keys(provider, tmp_path):
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(
            ["--repo", str(consumer), "--json", "configure"]
            + ["--capability", "hygiene", "--ci", provider]
        )
    assert code == 0, output.getvalue()
    manifest = (consumer / "gateseal.toml").read_text()
    assert tomllib.loads(manifest)["ci"] == {
        "provider": provider,
        "default_branch": "main",
    }
    assert f'[ci]\nprovider = "{provider}"\ndefault_branch = "main"\n\n' in manifest
    document = yaml.safe_load((consumer / PROVIDERS[provider].path).read_bytes())
    assert weekly(provider, document) is None
    assert json.loads(output.getvalue())["code"] == "configured"
