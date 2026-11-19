"""The Azure Pipelines file that checks the Lock and runs the CI gate.

The file is an owned output, as the GitHub workflow is. A branch's build
validation policy starts the build of a pull request, on the merge commit that
Azure Repos makes for it, so the file, the Lock, and the files the gate checks
come from one commit. Azure Pipelines names neither the target's commit nor
the commit a push replaced: the gate reads the first from the merge commit, and
a push checks every file and scans every commit reachable from it.

A job's own token reads a second repository only when the job names that
repository as a resource it uses, so the file names an Azure Repos policy.
A file that the team's own pipeline includes holds only the job, since the
team's pipeline owns the triggers and an included file declares no resources.
"""

import json
from urllib.parse import unquote

from gateseal.ci_providers.model import (
    GENERATED,
    IMAGE,
    SCHEDULE,
    GateJob,
    Triggers,
    flow,
    job_home,
    prepare,
    scoped_helper,
    script,
)
from gateseal.validation import string

PIPELINE = "azure-pipelines-gateseal.yml"

AZURE_REPOS = "https://dev.azure.com/"

PREPARE = prepare("$AGENT_TEMPDIRECTORY", 'echo "##vso[task.prependpath]$tools"')

GATE = (
    "set -euo pipefail\n"
    + job_home("$AGENT_TEMPDIRECTORY")
    + """\
gateseal="$AGENT_TEMPDIRECTORY/gateseal-command/bin/gateseal"
case "$GATESEAL_REASON" in
  PullRequest) "$gateseal" gate --merge "$GATESEAL_HEAD" ;;
  IndividualCI|BatchedCI) "$gateseal" gate --after "$GATESEAL_HEAD" ;;
  Schedule|Manual) "$gateseal" gate ;;
  *)
    echo "gateseal: this pipeline does not handle $GATESEAL_REASON builds." >&2
    exit 2
    ;;
esac
"""
)
"""The gate step. It uses no array, so that a Bash older than 4.4 runs it too."""

GATE_ENV = {
    "GATESEAL_REASON": "$(Build.Reason)",
    "GATESEAL_HEAD": "$(Build.SourceVersion)",
}

RUNNING = ("TF_BUILD", "True")

RUNNER_KEY = "pool"

DEMANDS = ["Agent.OS -equals Linux", "Agent.OSArchitecture -equals X64"]
"""What a self-hosted agent in a mixed pool must offer to run the job."""


def runner(value: object) -> tuple[str, ...]:
    """Return the self-hosted agent pool that the Manifest names."""
    return (string(value, f"ci.{RUNNER_KEY}"),)


def warning(message: str) -> str:
    """Return the logging command that Azure Pipelines shows as a warning."""
    escaped = message.replace("%", "%AZP25").replace("\r", "%0D").replace("\n", "%0A")
    return f"##vso[task.logissue type=warning]{escaped}"


TOKENS = {
    "GATESEAL_POLICY_TOKEN": "$(GATESEAL_POLICY_TOKEN)",
    "GATESEAL_JOB_TOKEN": "$(System.AccessToken)",
}
"""The policy token a pipeline sets as a secret variable, and the job's own.

Azure Pipelines leaves the reference to a variable that no one set as it is,
so the helper takes a value that still starts with a dollar sign and a
parenthesis as no token.
"""

CREDENTIAL_HELPER = (
    '!f() { test "$1" = get || return 0; token=$GATESEAL_POLICY_TOKEN; '
    'case "$token" in ""|[$]"("*) token=$GATESEAL_JOB_TOKEN ;; esac; '
    'echo username=x-access-token; echo "password=$token"; }; f'
)


RESOURCE = "gateseal_policy"
"""The pipeline's own name for the policy repository.

The resource names no ref. Azure Pipelines then resolves it to the default
branch of the repository, whatever that branch is called, and the job's token
reads every ref of the repository, the policy release's commit included.
"""


def resource_name(policy: str) -> str | None:
    """Return the name that a repository resource gives a policy URL, or None.

    The name is PROJECT/REPOSITORY of
    https://dev.azure.com/ORGANIZATION/PROJECT/_git/REPOSITORY, without percent
    escapes. Azure Pipelines looks for it in the organization of the pipeline,
    whatever the URL's organization is, and a run whose resource it cannot find
    does not start. None for a URL of any other shape, which has no such name.
    """
    parts = [unquote(part) for part in policy.removeprefix(AZURE_REPOS).split("/")]
    if len(parts) != 4 or parts[2] != "_git" or any("/" in part for part in parts):
        return None
    return f"{parts[1]}/{parts[3]}"


def pipeline(job: GateJob) -> bytes:
    """Return the pipeline for the Lock's gateseal and policy repository.

    Only a Git policy in Azure Repos over https gets a token, through a
    credential helper scoped to its repository. The job uses that repository
    as a resource, which lets the job's own token read it. A file that the
    team's pipeline includes keeps the job's use of the resource, and the
    team's pipeline declares it.
    """
    env: dict[str, str] = {}
    resources: list[str] = []
    uses: list[str] = []
    policy = job.policy_repository
    if policy is not None and policy.startswith(AZURE_REPOS):
        env |= TOKENS | scoped_helper(policy, CREDENTIAL_HELPER)
        name = resource_name(policy)
        if name is not None:
            resources = [
                "resources:",
                "  repositories:",
                f"    - repository: {RESOURCE}",
                "      type: git",
                f"      name: {json.dumps(name)}",
            ]
            uses = ["    uses:", "      repositories:", f"        - {RESOURCE}"]
    env |= job.pins()
    lines = [
        GENERATED,
        *([] if job.triggers is None else [*_triggers(job.triggers), *resources]),
        "jobs:",
        "  - job: gateseal",
        *_pool(job.runner),
        f"    timeoutInMinutes: {job.timeout_minutes}",
        *uses,
        "    steps:",
        "      - checkout: self",
        "        displayName: Check out the repository with its full history",
        "        clean: true",
        "        fetchDepth: 0",
        "        persistCredentials: false",
        *_step("Set up the locked gateseal, pre-commit and the policy", env, PREPARE),
        *_step("Run the gateseal gate", GATE_ENV, GATE),
    ]
    return ("\n".join(lines) + "\n").encode()


def _triggers(triggers: Triggers) -> list[str]:
    """Return the push trigger and, when the Manifest asks, the weekly run.

    The weekly run checks the default branch only, since Azure Pipelines
    starts one scheduled run for each branch its filter matches.
    """
    default = flow([triggers.default_branch])
    schedule = [
        "schedules:",
        f'  - cron: "{SCHEDULE}"',
        "    displayName: Weekly gateseal gate",
        "    branches:",
        f"      include: {default}",
        "    always: true",
    ]
    return [
        "trigger:",
        "  batch: false",
        "  branches:",
        f"    include: {flow((triggers.default_branch, *triggers.branches))}",
        *(schedule if triggers.schedule else []),
    ]


def _pool(runner: tuple[str, ...]) -> list[str]:
    """Return the hosted image, or the team's pool with the demands of the job."""
    if not runner:
        return ["    pool:", f"      vmImage: {IMAGE}"]
    [name] = runner
    return [
        "    pool:",
        f"      name: {json.dumps(name)}",
        "      demands:",
        *(f"        - {demand}" for demand in DEMANDS),
    ]


def _step(name: str, env: dict[str, str], lines: str) -> list[str]:
    """Return a bash step with its literal script and its environment."""
    return [
        "      - bash: |",
        *script(lines, "          "),
        f"        displayName: {name}",
        "        env:",
        *(f"          {key}: {value}" for key, value in env.items()),
    ]
