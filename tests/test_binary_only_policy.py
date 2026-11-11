"""A team policy whose tools are all release binaries, with no hook repository."""

import json

import pytest
from installation import clone, process, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

POLICY = """\
schema = 4
release = "binary-only"
minimum_compiler = "0.1.0"

[capabilities.hygiene]
tools = ["betterleaks"]

[tools.betterleaks]
configuration = "rules/betterleaks.toml"

[choices]

[components.betterleaks]
pin = "1.9.0"

[components.betterleaks.versions."1.9.0"]

[components.betterleaks.versions."1.8.0"]
"""


def binary_only(data):
    (data / "policy.toml").write_text(POLICY)
    (data / "rules/ruff.toml").unlink()
    lock = json.loads((data / "policy.lock").read_bytes())
    lock["components"] = {"betterleaks": lock["components"]["betterleaks"]}
    (data / "policy.lock").write_text(json.dumps(lock, indent=2) + "\n")


def test_a_policy_without_a_hook_repository_checks_commits(installed, tmp_path):
    origin = tmp_path / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", binary_only)
    env = environment(installed, origin)
    root = clone(tmp_path / "origin", tmp_path / "consumer")
    installed.cli(
        root,
        "configure",
        "--capability",
        "hygiene",
        "--policy",
        URL,
        "--policy-path",
        "policy",
        "--policy-tag",
        "v1",
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
        env=env,
    )
    installed.cli(root, "activate", env=env)
    assert installed.cli(root, "doctor", env=env)["code"] == "healthy"
    (root / "notes.txt").write_bytes(b"original\n")
    success(["git", "add", "-A"], root)
    commit = process(["git", "commit", "-m", "Adopt the policy"], root, env)
    assert commit.returncode == 0, commit.stdout + commit.stderr
    assert "Detect secrets with Betterleaks" in commit.stdout + commit.stderr
    assert installed.cli(root, "run", env=env)["code"] == "checks_passed"
