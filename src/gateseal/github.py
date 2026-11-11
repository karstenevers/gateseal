"""The GitHub Actions workflow that prepares the Lock and runs the CI gate.

The workflow is an owned output, so a reviewer reads it in a pull request's
diff and check refuses an edited one. The gate checks the commit GitHub runs
the workflow from, which for a pull request is its merge commit, so the
workflow, the Lock, and the files it checks come from one commit. Its jobs
never change with the selection: the gate runs the ci stage of the native
configuration, so a tool that runs only in CI needs no workflow branch.
Every value the Lock supplies is a JSON-quoted YAML scalar and reaches a script
only through env.
"""

import json

from gateseal.compiler_release import CompilerArtifact, load_runtime
from gateseal.diagnostics import GatesealError
from gateseal.manifest import CI, GitSource, Source

WORKFLOW = ".github/workflows/gateseal.yml"

CHECKOUT = "actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5"
"""The checkout pin this repository's own workflows use."""

UV_URL = (
    "https://github.com/astral-sh/uv/releases/download/0.13.6/"
    "uv-x86_64-unknown-linux-gnu.tar.gz"
)
UV_SHA256 = "f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645"
MISE_URL = (
    "https://github.com/jdx/mise/releases/download/v2026.10.19/"
    "mise-v2026.10.19-linux-x64"
)
MISE_SHA256 = "3162760f40b6b5f706c60641e4ef5ee7322bc6c54f72fc26a7111b337aa18d9d"

PREPARE = """\
set -euo pipefail
umask 077
tools="$RUNNER_TEMP/gateseal-tools"
mkdir -p "$tools"
curl --fail --location --silent --show-error "$GATESEAL_UV_URL" \\
  --output "$tools/uv.tar.gz"
echo "$GATESEAL_UV_SHA256  $tools/uv.tar.gz" | sha256sum --check -
tar -xzf "$tools/uv.tar.gz" -C "$tools" --strip-components=1
curl --fail --location --silent --show-error "$GATESEAL_MISE_URL" \\
  --output "$tools/mise"
echo "$GATESEAL_MISE_SHA256  $tools/mise" | sha256sum --check -
chmod 0755 "$tools/uv" "$tools/mise"
export PATH="$tools:$PATH"
echo "$tools" >> "$GITHUB_PATH"
command="$RUNNER_TEMP/gateseal-command"
uv venv --no-config --managed-python --python "$GATESEAL_PYTHON" "$command"
printf '%s\\n' "$GATESEAL_COMPILER" > "$RUNNER_TEMP/gateseal-compiler.txt"
uv pip install --no-config --python "$command/bin/python" --require-hashes \\
  --no-deps --only-binary :all: -r "$RUNNER_TEMP/gateseal-compiler.txt"
"$command/bin/gateseal" activate
"""

GATE = """\
set -euo pipefail
case "$GATESEAL_EVENT" in
  pull_request) scope=(--base "$GATESEAL_PR_BASE" --head "$GATESEAL_HEAD") ;;
  merge_group) scope=(--base "$GATESEAL_GROUP_BASE" --head "$GATESEAL_HEAD") ;;
  push) scope=(--before "$GATESEAL_PUSH_BEFORE" --after "$GATESEAL_PUSH_AFTER") ;;
  schedule|workflow_dispatch) scope=() ;;
  *)
    echo "gateseal: this workflow does not handle $GATESEAL_EVENT events." >&2
    exit 2
    ;;
esac
"$RUNNER_TEMP/gateseal-command/bin/gateseal" gate "${scope[@]}"
"""

GATE_ENV = {
    "GATESEAL_EVENT": "${{ github.event_name }}",
    "GATESEAL_HEAD": "${{ github.sha }}",
    "GATESEAL_PR_BASE": "${{ github.event.pull_request.base.sha }}",
    "GATESEAL_GROUP_BASE": "${{ github.event.merge_group.base_sha }}",
    "GATESEAL_PUSH_BEFORE": "${{ github.event.before }}",
    "GATESEAL_PUSH_AFTER": "${{ github.event.after }}",
}

CREDENTIAL_HELPER = (
    '!f() { test "$1" = get && echo username=x-access-token && '
    'echo "password=$GATESEAL_POLICY_TOKEN"; }; f'
)


def workflow(ci: CI, artifact: CompilerArtifact, source: Source) -> bytes:
    """Return the workflow for the Lock's compiler and policy repository.

    A fresh runner must download the compiler, so a local wheel cannot run
    it, and it downloads without credentials, so the wheel's URL must be
    public. Only a Git policy on GitHub over https gets a token, through a
    credential helper scoped to its repository; manifest.REPOSITORY already
    keeps that URL to characters a Git configuration key can name.
    """
    if artifact.kind != "https-wheel":
        raise GatesealError(
            "ci_needs_network_compiler",
            "The CI workflow downloads the locked compiler on a fresh runner, but "
            "the Lock pins a local wheel.",
            next_step="Move gateseal.lock aside and run gateseal configure with "
            "--compiler-source and --compiler-sha256.",
        )
    record = next(iter(load_runtime().python.values()))
    python = ".".join(str(record[part]) for part in ("major", "minor", "patch"))
    env: dict[str, str] = {}
    if isinstance(source, GitSource) and source.repository.startswith(
        "https://github.com/"
    ):
        env |= {
            "GATESEAL_POLICY_TOKEN": "${{ secrets.GATESEAL_POLICY_TOKEN || "
            "github.token }}",
            "GIT_CONFIG_COUNT": json.dumps("3"),
            "GIT_CONFIG_KEY_0": "credential.useHttpPath",
            "GIT_CONFIG_VALUE_0": json.dumps("true"),
            "GIT_CONFIG_KEY_1": "credential.helper",
            "GIT_CONFIG_VALUE_1": json.dumps(""),
            "GIT_CONFIG_KEY_2": json.dumps(f"credential.{source.repository}.helper"),
            "GIT_CONFIG_VALUE_2": json.dumps(CREDENTIAL_HELPER),
        }
    env |= {
        "GATESEAL_UV_URL": json.dumps(UV_URL),
        "GATESEAL_UV_SHA256": json.dumps(UV_SHA256),
        "GATESEAL_MISE_URL": json.dumps(MISE_URL),
        "GATESEAL_MISE_SHA256": json.dumps(MISE_SHA256),
        "GATESEAL_PYTHON": json.dumps(python),
        "GATESEAL_COMPILER": json.dumps(
            f"gateseal @ {artifact.location} --hash=sha256:{artifact.sha256}"
        ),
    }
    lines = [
        "# Generated by gateseal from gateseal.lock. Run gateseal configure to "
        "change it; do not edit it.",
        "name: gateseal",
        "on:",
        "  push:",
        f"    branches: [{json.dumps(ci.default_branch)}]",
        "  pull_request:",
        "  merge_group:",
        "  workflow_dispatch:",
        "  schedule:",
        '    - cron: "37 4 * * 1"',
        "permissions:",
        "  contents: read",
        "jobs:",
        "  gateseal:",
        "    name: ${{ github.event_name == 'push' && 'gateseal-push' || 'gateseal' }}",
        "    runs-on: ubuntu-24.04",
        "    timeout-minutes: 30",
        "    steps:",
        "      - name: Check out the repository with its full history",
        f"        uses: {CHECKOUT}",
        "        with:",
        "          fetch-depth: 0",
        "          persist-credentials: false",
        *_step("Prepare the locked compiler, policy and checks", env, PREPARE),
        *_step("Run the gateseal gate", GATE_ENV, GATE),
    ]
    return ("\n".join(lines) + "\n").encode()


def _step(name: str, env: dict[str, str], script: str) -> list[str]:
    """Return a bash step with its environment and its literal script."""
    return [
        f"      - name: {name}",
        "        shell: bash",
        "        env:",
        *(f"          {key}: {value}" for key, value in env.items()),
        "        run: |",
        *(f"          {line}" if line else "" for line in script.splitlines()),
    ]
