"""A team policy whose secret rules start from the rules built into Betterleaks."""

import pytest
from installation import clone, process, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

PASSWORD = "hT9qL2vR8" + "mX4zKp3W"


def allow_passwords(data):
    rules = data / "rules/betterleaks.toml"
    text = rules.read_text()
    assert "disabledRules = [\n" in text
    rules.write_text(
        text.replace(
            "disabledRules = [\n", 'disabledRules = [\n  "generic-password",\n'
        )
    )


def test_a_team_policy_turns_off_one_built_in_rule(installed, tmp_path):
    origin = tmp_path / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", allow_passwords)
    env = environment(installed, origin)
    root = clone(tmp_path / "origin", tmp_path / "consumer")
    installed.cli(
        root,
        "configure",
        "--capability",
        "secrets",
        "--policy",
        URL,
        "--policy-path",
        "policy",
        "--policy-tag",
        "v1",
        env=env,
    )
    success(["git", "add", "-A"], root)
    success(["git", "commit", "-m", "Adopt the policy"], root, env)
    settings = root / "settings.py"
    settings.write_text(f'password = "{PASSWORD}"\n')
    success(["git", "add", settings.name], root)
    success(["git", "commit", "-m", "Add a password"], root, env)
    settings.write_text(f'client_secret = "{PASSWORD}{PASSWORD}"\n')
    success(["git", "add", settings.name], root)
    blocked = process(["git", "commit", "-m", "Add a client secret"], root, env)
    output = blocked.stdout + blocked.stderr
    assert blocked.returncode == 1, output
    assert "settings.py:1:8: generic-api-key" in output, output
    assert PASSWORD not in output
