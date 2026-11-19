"""The team policy and Manifest that the docs show agree and configure together.

The policy is published into a local Git repository that the example's
repository URL reaches through url.<base>.insteadOf, then the Manifest is
configured against it in process.
"""

import json
import os
import tomllib

from conftest import with_git_config
from doc_examples import example
from gateseal.validation import canonical
from team_policy import git, publish
from test_lock_configure import MANIFEST, configure, worktree

POLICY = example("policy-format.md", "policy tables: team")
MANIFEST_TEXT = example("policy-contract.md", "manifest tables: team")


def test_the_example_manifest_configures_against_the_example_policy(
    tmp_path, monkeypatch
):
    origin = tmp_path / "policy-repository"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")

    def team_policy(data):
        (data / "policy.toml").write_text(POLICY)
        tools = {
            tool
            for capability in tomllib.loads(POLICY)["capabilities"].values()
            for tool in capability["tools"]
        }
        locked = json.loads((data / "policy.lock").read_bytes())
        locked.pop("node")
        locked["components"] = {
            name: record
            for name, record in locked["components"].items()
            if name in tools
        }
        (data / "policy.lock").write_bytes(canonical(locked))

    commit = publish(origin, "team-2026.11", team_policy)
    manifest = tomllib.loads(MANIFEST_TEXT)
    settings = {f"url.{origin.as_uri()}.insteadOf": manifest["policy"]["repository"]}
    for key, value in with_git_config(dict(os.environ), settings).items():
        if key.startswith("GIT_CONFIG_"):
            monkeypatch.setenv(key, value)

    root = worktree(tmp_path)
    placeholder = manifest["policy"]["commit"]
    (root / MANIFEST).write_text(MANIFEST_TEXT.replace(placeholder, commit))
    configure(root)

    written = json.loads((root / "gateseal.lock").read_text())
    assert written["policy"]["release"] == "team-2026.11"
    assert written["policy"]["tag"] == manifest["policy"]["tag"]
    assert written["policy"]["commit"] == commit
    assert written["choices"] == {
        "hygiene": {"exclude": [], "skip": ["check-yaml"]},
        "python": {"target_version": "py312"},
    }
    ruff = tomllib.loads(POLICY)["capabilities"]["python"]["tools"]["ruff"]
    assert written["components"]["ruff"]["version"] == ruff["pin"]
    assert "check-yaml" not in written["components"]["pre-commit-hooks"]["hook_ids"]
