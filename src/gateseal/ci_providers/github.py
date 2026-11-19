"""The GitHub Actions workflow that checks the Lock and runs the CI gate.

The workflow is an owned output, so a reviewer reads it in a pull request's
diff and check refuses an edited one. The gate checks the commit GitHub runs
the workflow from, which for a pull request is its merge commit, so the
workflow, the Lock, and the files it checks come from one commit. Its jobs
never change with the selection: the gate runs the ci stage of the native
configuration, so a tool that runs only in CI needs no workflow branch. A new
push to a pull request cancels its older run, while every other run gets a
concurrency group of its own, so it never cancels or waits for another. A
workflow that another workflow calls has neither triggers nor concurrency of
its own: the caller's workflow owns both.
"""

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
from gateseal.diagnostics import GatesealError
from gateseal.validation import sequence, string

WORKFLOW = ".github/workflows/gateseal.yml"

CHECKOUT = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
"""The checkout pin this repository's own workflows use."""

PREPARE = prepare("$RUNNER_TEMP", 'echo "$tools" >> "$GITHUB_PATH"')

GATE = (
    "set -euo pipefail\n"
    + job_home("$RUNNER_TEMP")
    + """\
case "$GATESEAL_EVENT" in
  pull_request) set -- --base "$GATESEAL_PR_BASE" --head "$GATESEAL_HEAD" ;;
  merge_group) set -- --base "$GATESEAL_GROUP_BASE" --head "$GATESEAL_HEAD" ;;
  push) set -- --before "$GATESEAL_PUSH_BEFORE" --after "$GATESEAL_PUSH_AFTER" ;;
  schedule|workflow_dispatch) set -- ;;
  *)
    echo "gateseal: this workflow does not handle $GATESEAL_EVENT events." >&2
    exit 2
    ;;
esac
"$RUNNER_TEMP/gateseal-command/bin/gateseal" gate "$@"
"""
)
"""The gate step. It uses no array, so that a Bash older than 4.4 runs it too."""

GATE_ENV = {
    "GATESEAL_EVENT": "${{ github.event_name }}",
    "GATESEAL_HEAD": "${{ github.sha }}",
    "GATESEAL_PR_BASE": "${{ github.event.pull_request.base.sha }}",
    "GATESEAL_GROUP_BASE": "${{ github.event.merge_group.base_sha }}",
    "GATESEAL_PUSH_BEFORE": "${{ github.event.before }}",
    "GATESEAL_PUSH_AFTER": "${{ github.event.after }}",
}

RUNNING = ("GITHUB_ACTIONS", "true")

RUNNER_KEY = "runs_on"

CONCURRENCY = [
    "concurrency:",
    "  group: gateseal-${{ github.event_name }}-"
    "${{ github.event.pull_request.number || github.run_id }}",
    "  cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
]

CALLED = [
    "  workflow_call:",
    "    secrets:",
    "      GATESEAL_POLICY_TOKEN:",
    "        required: false",
]
"""The trigger of a workflow that another workflow calls.

A called workflow sees only the secrets its caller passes, so it declares the
policy token, which a caller may leave out.
"""


def runner(value: object) -> tuple[str, ...]:
    """Return the runner labels that the Manifest lists, at least one."""
    field = f"ci.{RUNNER_KEY}"
    labels = tuple(string(label, field) for label in sequence(value, field))
    if not labels:
        raise GatesealError(
            "invalid_field", f"{field} must list at least one runner label."
        )
    return labels


def warning(message: str) -> str:
    """Return the workflow command that GitHub shows as a warning annotation."""
    escaped = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return f"::warning title=gateseal::{escaped}"


CREDENTIAL_HELPER = (
    '!f() { test "$1" = get && echo username=x-access-token && '
    'echo "password=$GATESEAL_POLICY_TOKEN"; }; f'
)


def workflow(job: GateJob) -> bytes:
    """Return the workflow for the Lock's gateseal and policy repository.

    Only a Git policy on GitHub over https gets a token, through a credential
    helper scoped to its repository.
    """
    env: dict[str, str] = {}
    policy = job.policy_repository
    if policy is not None and policy.startswith("https://github.com/"):
        env |= {
            "GATESEAL_POLICY_TOKEN": "${{ secrets.GATESEAL_POLICY_TOKEN || "
            "github.token }}",
        } | scoped_helper(policy, CREDENTIAL_HELPER)
    env |= job.pins()
    lines = [
        GENERATED,
        "name: gateseal",
        "on:",
        *_events(job.triggers),
        "permissions:",
        "  contents: read",
        *(CONCURRENCY if job.triggers is not None else []),
        "jobs:",
        "  gateseal:",
        "    name: ${{ github.event_name == 'push' && 'gateseal-push' || 'gateseal' }}",
        f"    runs-on: {flow(job.runner) if job.runner else IMAGE}",
        f"    timeout-minutes: {job.timeout_minutes}",
        "    steps:",
        "      - name: Check out the repository with its full history",
        f"        uses: {CHECKOUT}",
        "        with:",
        "          fetch-depth: 0",
        "          persist-credentials: false",
        *_step("Set up the locked gateseal, pre-commit and the policy", env, PREPARE),
        *_step("Run the gateseal gate", GATE_ENV, GATE),
    ]
    return ("\n".join(lines) + "\n").encode()


def _events(triggers: Triggers | None) -> list[str]:
    """Return the events that start the workflow.

    GitHub's * stops at a /, so a branch entry's final /* becomes /**, which
    matches every branch below it.
    """
    if triggers is None:
        return CALLED
    branches = (
        branch + "*" if branch.endswith("/*") else branch
        for branch in triggers.branches
    )
    schedule = ["  schedule:", f'    - cron: "{SCHEDULE}"']
    return [
        "  push:",
        f"    branches: {flow((triggers.default_branch, *branches))}",
        "  pull_request:",
        "  merge_group:",
        "  workflow_dispatch:",
        *(schedule if triggers.schedule else []),
    ]


def _step(name: str, env: dict[str, str], lines: str) -> list[str]:
    """Return a bash step with its environment and its literal script."""
    return [
        f"      - name: {name}",
        "        shell: bash",
        "        env:",
        *(f"          {key}: {value}" for key, value in env.items()),
        "        run: |",
        *script(lines, "          "),
    ]
