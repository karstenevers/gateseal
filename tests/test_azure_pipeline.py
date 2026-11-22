"""The Azure Pipelines file: its triggers, its agent, and each build reason.

The gate step's script runs here as the agent runs it, with a stand-in for the
installed gateseal that prints the arguments it gets.
"""

from pathlib import Path

import pytest
from gate_files import BRANCH, SERVED, credential, gate_file
from gateseal.ci_providers import azure
from gateseal.diagnostics import Annotation
from installation import process

HEAD = "a" * 40
EXPECTED = Path(__file__).with_name("expected_azure_pipeline.yml")


def test_the_pipeline_equals_the_reviewed_file():
    assert gate_file("azure", SERVED["azure"]).data == EXPECTED.read_bytes()


def test_a_push_to_the_default_branch_starts_one_build_each():
    trigger = gate_file("azure").document["trigger"]
    assert trigger == {"batch": False, "branches": {"include": [BRANCH]}}


def test_the_checkout_removes_what_an_earlier_run_left_on_the_agent():
    assert gate_file("azure").checkout["clean"] is True


def test_pull_request_builds_are_left_to_the_branch_policy():
    assert "pr" not in gate_file("azure").document


def test_one_job_runs_on_the_hosted_linux_image_with_a_time_limit():
    [job] = gate_file("azure").document["jobs"]
    assert job["job"] == "gateseal"
    assert job["pool"] == {"vmImage": "ubuntu-24.04"}
    assert job["timeoutInMinutes"] == 30
    assert [next(iter(step)) for step in job["steps"]] == [
        "checkout",
        "bash",
        "bash",
        "bash",
    ]
    assert job["steps"][0]["checkout"] == "self"


def test_preparation_puts_its_tools_on_the_path_of_later_steps():
    script = gate_file("azure").prepare.script
    assert 'echo "##vso[task.prependpath]$tools"' in script.splitlines()
    assert 'tools="$AGENT_TEMPDIRECTORY/gateseal-tools"' in script.splitlines()


def test_the_gate_reads_the_build_from_predefined_variables():
    assert gate_file("azure").gate.env == {
        "GATESEAL_REASON": "$(Build.Reason)",
        "GATESEAL_HEAD": "$(Build.SourceVersion)",
    }


def gate(reason, directory):
    """Run the gate step for a build of reason, and return what gateseal got."""
    command = directory / "gateseal-command" / "bin" / "gateseal"
    command.parent.mkdir(parents=True)
    command.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    command.chmod(0o755)
    environment = {
        "PATH": "/usr/bin:/bin",
        "AGENT_TEMPDIRECTORY": str(directory),
        "GATESEAL_REASON": reason,
        "GATESEAL_HEAD": HEAD,
    }
    ran = process(
        ["bash", "-c", gate_file("azure").gate.script], directory, environment
    )
    return ran.returncode, ran.stdout.split(), ran.stderr


@pytest.mark.parametrize(
    "reason,arguments",
    [
        ("PullRequest", ["gate", "--merge", HEAD]),
        ("IndividualCI", ["gate", "--after", HEAD]),
        ("BatchedCI", ["gate", "--after", HEAD]),
        ("Schedule", ["gate"]),
        ("Manual", ["gate"]),
    ],
)
def test_each_build_reason_names_its_scope(reason, arguments, tmp_path):
    assert gate(reason, tmp_path) == (0, arguments, "")


@pytest.mark.parametrize("reason", ["BuildCompletion", "ResourceTrigger", ""])
def test_a_build_reason_without_a_scope_stops_before_the_gate(reason, tmp_path):
    code, ran, error = gate(reason, tmp_path)
    assert (code, ran) == (2, [])
    assert error == f"gateseal: this pipeline does not handle {reason} builds.\n"


def test_a_failed_gate_fails_the_step(tmp_path):
    command = tmp_path / "gateseal-command" / "bin" / "gateseal"
    command.parent.mkdir(parents=True)
    command.write_text("#!/bin/sh\nexit 1\n")
    command.chmod(0o755)
    environment = {
        "PATH": "/usr/bin:/bin",
        "AGENT_TEMPDIRECTORY": str(tmp_path),
        "GATESEAL_REASON": "Manual",
        "GATESEAL_HEAD": HEAD,
    }
    script = gate_file("azure").gate.script
    assert process(["bash", "-c", script], tmp_path, environment).returncode == 1


@pytest.mark.parametrize(
    "policy_token,password",
    [
        ("team-token", "team-token"),
        ("", "job-token"),
        ("$(GATESEAL_POLICY_TOKEN)", "job-token"),
    ],
    ids=["a secret variable", "an empty variable", "no variable"],
)
def test_the_policy_token_is_the_secret_variable_or_else_the_jobs(
    policy_token, password, tmp_path
):
    step = gate_file("azure", SERVED["azure"]).prepare
    assert step.env["GATESEAL_POLICY_TOKEN"] == "$(GATESEAL_POLICY_TOKEN)"
    assert step.env["GATESEAL_JOB_TOKEN"] == "$(System.AccessToken)"
    tokens = {"GATESEAL_POLICY_TOKEN": policy_token, "GATESEAL_JOB_TOKEN": "job-token"}
    assert credential(step, SERVED["azure"], tokens, tmp_path) == password


def test_the_job_uses_an_azure_repos_policy_as_a_resource():
    document = gate_file("azure", SERVED["azure"]).document
    assert document["resources"] == {
        "repositories": [
            {
                "repository": "gateseal_policy",
                "type": "git",
                "name": "platform/gateseal-policy",
            }
        ]
    }
    [job] = document["jobs"]
    assert job["uses"] == {"repositories": ["gateseal_policy"]}


def test_a_resource_names_the_project_and_repository_without_escapes():
    url = "https://dev.azure.com/team/Platform%20Team/_git/gateseal%22policy"
    [resource] = gate_file("azure", url).document["resources"]["repositories"]
    assert resource["name"] == 'Platform Team/gateseal"policy'


@pytest.mark.parametrize(
    "repository",
    [
        None,
        SERVED["github"],
        SERVED["azure"].replace("https://", "ssh://git@"),
        "https://dev.azure.com/team/_git/gateseal-policy",
        "https://dev.azure.com/team/platform/git/gateseal-policy",
        "https://dev.azure.com/team/_git/platform/gateseal-policy",
        "https://dev.azure.com/team/platform/_git/_full/gateseal-policy",
        "https://dev.azure.com/team/plat%2Fform/_git/gateseal-policy",
        "https://dev.azure.com/team/platform/_git/gateseal%2Fpolicy",
    ],
    ids=[
        "bundled",
        "another host",
        "ssh",
        "no project",
        "no _git",
        "_git first",
        "a longer path",
        "a slash in the project",
        "a slash in the repository",
    ],
)
def test_a_policy_without_a_resource_name_is_no_resource(repository):
    document = gate_file("azure", repository).document
    [job] = document["jobs"]
    assert "resources" not in document
    assert "uses" not in job


def test_a_policy_url_of_another_shape_still_gets_the_token(tmp_path):
    url = "https://dev.azure.com/team/_git/gateseal-policy"
    step = gate_file("azure", url).prepare
    tokens = {"GATESEAL_POLICY_TOKEN": "team-token", "GATESEAL_JOB_TOKEN": "job-token"}
    assert credential(step, url, tokens, tmp_path) == "team-token"


def test_the_credential_helper_holds_no_reference_that_the_agent_expands():
    assert "$(" not in azure.CREDENTIAL_HELPER


def test_a_warning_is_a_logging_command_with_its_own_escapes():
    assert azure.annotation(Annotation("warning", "100%\r\nover; [budget]")) == (
        "##vso[task.logissue type=warning]100%AZP25%0D%0Aover; [budget]"
    )
