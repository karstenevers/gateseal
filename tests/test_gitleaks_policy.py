"""A team adds Gitleaks to its own policy, as docs/add-a-tool.md shows.

The policy's tools/gitleaks.toml and rules/gitleaks.toml are the files the
how-to publishes, so the example a team copies is the one these tests run.
A consumer adopts the release from a real Git repository, and mise installs
Gitleaks from its GitHub release.
"""

import json
import re
import subprocess
import textwrap
from pathlib import Path

import pytest
from gateseal.validation import canonical
from installation import clone, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

DOC = Path(__file__).parents[1] / "docs" / "add-a-tool.md"
SECRET = "AKIA" + "Q7XK2M9P4R8T6W3Z"
RELEASES = "https://github.com/gitleaks/gitleaks/releases/download"
ARCHIVES = {
    "8.30.0": {
        "linux-x64": (
            "linux_x64",
            "79a3ab579b53f71efd634f3aaf7e04a0fa0cf206b7ed434638d1547a2470a66e",
        ),
        "macos-arm64": (
            "darwin_arm64",
            "b251ab2bcd4cd8ba9e56ff37698c033ebf38582b477d21ebd86586d927cf87e7",
        ),
    },
    "8.30.1": {
        "linux-x64": (
            "linux_x64",
            "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb",
        ),
        "macos-arm64": (
            "darwin_arm64",
            "b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5",
        ),
    },
}
"""Each platform's archive and the SHA-256 its release's checksum file lists."""

USE = """
[tools.gitleaks]
configuration = "rules/gitleaks.toml"

[components.gitleaks]
pin = "8.30.0"

[components.gitleaks.versions."8.30.0"]
"""


def example(name):
    """Return the file the how-to shows under <!-- policy file: name -->."""
    found = re.search(
        rf"^( *)<!-- policy file: {re.escape(name)} -->\n\1```toml\n(.*?)^\1```$",
        DOC.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert found, f"{DOC.name} shows no {name}"
    return textwrap.dedent(found[2])


def record(version):
    """Return the Policy Lock record gateseal policy lock writes for version."""
    return {
        "platforms": {
            platform: {
                "executable": "gitleaks",
                "sha256": sha256,
                "url": f"{RELEASES}/v{version}/gitleaks_{version}_{name}.tar.gz",
            }
            for platform, (name, sha256) in ARCHIVES[version].items()
        }
    }


def add_gitleaks(data, locked=True):
    """Add Gitleaks to the secrets capability of the policy in data."""
    for name in ("tools/gitleaks.toml", "rules/gitleaks.toml"):
        (data / name).parent.mkdir(exist_ok=True)
        (data / name).write_text(example(name))
    policy = data / "policy.toml"
    text = policy.read_text()
    assert 'tools = ["betterleaks"]' in text
    policy.write_text(
        text.replace('tools = ["betterleaks"]', 'tools = ["betterleaks", "gitleaks"]')
        + USE
    )
    lock = json.loads((data / "policy.lock").read_bytes())
    lock["components"]["gitleaks"] = {"versions": {"8.30.0": record("8.30.0")}}
    if locked:
        (data / "policy.lock").write_bytes(canonical(lock))


@pytest.fixture(scope="module")
def guarded(installed, tmp_path_factory):
    """A consumer whose secrets capability runs Betterleaks and Gitleaks."""
    origin = tmp_path_factory.mktemp("gitleaks policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", add_gitleaks)
    root = tmp_path_factory.mktemp("gitleaks consumer ü")
    consumer = clone(root / "origin", root / "consumer")
    env = environment(installed, origin)
    installed.cli(
        consumer,
        "configure",
        *("--capability", "hygiene", "--capability", "secrets"),
        *("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1"),
        *("--compiler-wheel", installed.wheel),
        *("--compiler-sha256", installed.checksum),
        env=env,
    )
    assert installed.cli(consumer, "activate", env=env)["code"] == "prepared"
    success(["git", "add", "-A"], consumer)
    success(["git", "commit", "-m", "Adopt the team policy"], consumer, env)
    return consumer, env


def tool_notes(result):
    return [d["message"] for d in result["diagnostics"] if d["category"] == "tool"]


def test_status_and_doctor_label_gitleaks_team_defined(installed, guarded):
    consumer, env = guarded
    for command in ("status", "doctor"):
        assert tool_notes(installed.cli(consumer, command, env=env)) == [
            "Tool pre-commit-hooks 6.0.0 for hygiene: built-in, pre-commit-repository.",
            "Tool betterleaks 1.9.0 for secrets: built-in, release-binary.",
            "Tool gitleaks 8.30.0 for secrets: team-defined, release-binary; it "
            "carries the tool-specific guarantees only as far as its "
            "definition's arguments declare them.",
        ]
    config = json.loads((consumer / ".pre-commit-config.yaml").read_bytes())
    entries = [h.get("entry", "") for repo in config["repos"] for h in repo["hooks"]]
    assert any(entry.startswith("gateseal-gitleaks dir --config") for entry in entries)


def test_a_staged_secret_stops_the_commit_without_printing_it(guarded):
    consumer, env = guarded
    (consumer / "leak.txt").write_text(f"key = {SECRET}\n")
    success(["git", "add", "leak.txt"], consumer)
    try:
        failed = subprocess.run(
            ["git", "commit", "-m", "Add a key"],
            cwd=consumer,
            env=env,
            capture_output=True,
            text=True,
        )
        output = failed.stdout + failed.stderr
        assert failed.returncode == 1, output
        assert re.search(r"Detect secrets with Gitleaks\.+Failed", output), output
        assert re.search(r"Detect secrets with Betterleaks\.+Failed", output), output
        assert re.search(
            r"leak\.txt:1:[0-9]+: aws-access-token \(AWS access key identifier\)",
            output,
        ), output
        assert SECRET not in output
    finally:
        success(["git", "rm", "--cached", "-q", "leak.txt"], consumer)
        (consumer / "leak.txt").unlink()


def test_two_staged_files_make_gitleaks_scan_the_worktree(guarded):
    """Gitleaks scans one path, so two files make it scan everything, as documented."""
    consumer, env = guarded
    (consumer / "kept.env").write_text(f"key = {SECRET}\n")
    for name in ("one.txt", "two.txt"):
        (consumer / name).write_text("clean\n")
    success(["git", "add", "one.txt", "two.txt"], consumer)
    try:
        failed = subprocess.run(
            ["git", "commit", "-m", "Add two files"],
            cwd=consumer,
            env=env,
            capture_output=True,
            text=True,
        )
        output = failed.stdout + failed.stderr
        assert failed.returncode == 1, output
        assert re.search(r"Detect secrets with Betterleaks\.+Passed", output), output
        assert re.search(r"kept\.env:1:[0-9]+: aws-access-token", output), output
        assert ".git/" not in output and SECRET not in output
    finally:
        success(["git", "rm", "--cached", "-q", "one.txt", "two.txt"], consumer)
        for name in ("kept.env", "one.txt", "two.txt"):
            (consumer / name).unlink()


def test_run_reports_the_team_finding_with_its_origin(installed, guarded):
    consumer, env = guarded
    (consumer / "leak.txt").write_text(f"key = {SECRET}\n")
    success(["git", "add", "leak.txt"], consumer)
    try:
        failed = installed.cli(consumer, "run", code=1, env=env)
        found = [
            {key: d[key] for key in ("category", "origin", "hook", "rule")}
            for d in failed["diagnostics"]
            if d.get("tool") == "gitleaks" and d["category"] == "finding"
        ]
        # Each batch of files that pre-commit passes makes Gitleaks scan the
        # whole worktree, so a run can report one finding more than once.
        assert found and all(
            entry
            == {
                "category": "finding",
                "origin": "team-defined",
                "hook": "gitleaks",
                "rule": "aws-access-token",
            }
            for entry in found
        )
        assert SECRET not in json.dumps(failed)
    finally:
        success(["git", "rm", "--cached", "-q", "leak.txt"], consumer)
        (consumer / "leak.txt").unlink()


@pytest.mark.network
def test_policy_lock_and_add_review_gitleaks_from_github(installed, tmp_path):
    """Lock the how-to's policy, then review Gitleaks 8.30.1, from GitHub."""
    policy = tmp_path / "policy"
    installed.cli(tmp_path, "policy", "init", str(policy))
    add_gitleaks(policy, locked=False)
    installed.cli(tmp_path, "policy", "lock", str(policy))
    added = installed.cli(tmp_path, "policy", "add", "gitleaks", "8.30.1", str(policy))
    assert added["code"] == "added"
    lock = json.loads((policy / "policy.lock").read_bytes())
    assert lock["components"]["gitleaks"] == {
        "versions": {version: record(version) for version in ARCHIVES}
    }
    pinned = installed.cli(tmp_path, "policy", "pin", "gitleaks", "8.30.1", str(policy))
    assert pinned["code"] == "pinned"
    assert installed.cli(tmp_path, "policy", "check", str(policy))["code"] == (
        "consistent"
    )
