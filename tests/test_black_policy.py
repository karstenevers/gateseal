"""A team offers Black as its formatter, as docs/add-a-tool.md shows.

A release of a team policy offers the how-to's Black alternative. A consumer
picks Black in gateseal.toml and commits a file that Black reformats. The first
commit clones Black's mirror from GitHub and installs Black from PyPI.
"""

import json
import subprocess

import pytest
from black_policy import REVISION, add_black
from conftest import on_machine
from installation import clone, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

CHOICE = '\n[choices.python]\nformatter = "black"\n'
UNFORMATTED = "config=dict(a=1)\n"
FORMATTED = "config = dict(a=1)\n"


@pytest.fixture(scope="module")
def formatted_with_black(installed, tmp_path_factory):
    """A consumer of the how-to's policy that picked Black, and its environment."""
    origin = tmp_path_factory.mktemp("black policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", add_black)
    root = tmp_path_factory.mktemp("black consumer ü")
    env = environment(installed, origin)
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        installed.cli(
            consumer,
            "configure",
            *("--capability", "hygiene", "--capability", "python"),
            *("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1"),
            env=env,
        )
        manifest = consumer / "gateseal.toml"
        manifest.write_text(manifest.read_text() + CHOICE)
        installed.cli(consumer, "configure", env=env)
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-m", "Format with Black"], consumer, env)
    return consumer, env


def commit(consumer, env, name, content):
    (consumer / name).write_text(content)
    success(["git", "add", name], consumer)
    result = subprocess.run(
        ["git", "commit", "-m", f"Add {name}"],
        cwd=consumer,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, result.stdout + result.stderr


def test_status_and_doctor_name_the_alternative(installed, formatted_with_black):
    consumer, env = formatted_with_black
    for command in ("status", "doctor"):
        result = installed.cli(consumer, command, env=env)
        choices = [
            d["message"]
            for d in result["diagnostics"]
            if d["category"] == "policy_choice"
        ]
        assert "Choice python.formatter: black." in choices
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["components"]["black"]["revision"] == REVISION
    assert lock["components"]["ruff"]["hook_ids"] == ["ruff-check"]


def test_black_formats_what_a_commit_stages(formatted_with_black):
    consumer, env = formatted_with_black
    result, output = commit(consumer, env, "sample.py", UNFORMATTED)
    assert result.returncode == 1, output
    assert "- hook id: black\n- files were modified by this hook" in output, output
    assert "ruff format" not in output
    assert (consumer / "sample.py").read_text() == FORMATTED
    result, output = commit(consumer, env, "sample.py", FORMATTED)
    assert result.returncode == 0, output


@pytest.mark.network
def test_policy_lock_reviews_black_from_github(installed, tmp_path):
    """Lock the how-to's policy from GitHub."""
    policy = tmp_path / "policy"
    installed.cli(tmp_path, "policy", "init", str(policy))
    add_black(policy, locked=False)
    installed.cli(tmp_path, "policy", "lock", str(policy))
    lock = json.loads((policy / "policy.lock").read_bytes())
    assert lock["components"]["black"] == {
        "versions": {"26.10.1": {"revision": REVISION}}
    }
    assert installed.cli(tmp_path, "policy", "check", str(policy))["code"] == (
        "consistent"
    )
