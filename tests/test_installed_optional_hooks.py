"""A repository skips the hooks that a team policy marks as optional.

A release of a team policy marks check-yaml and the Betterleaks hook as
optional. One consumer skips both, another keeps the defaults. A commit,
gateseal run and gateseal gate run pre-commit on the same generated
configuration, so all three skip a hook that the Manifest skips. The gate runs
no history scan of a tool whose hooks are all skipped.
"""

import json

import pytest
from conftest import on_machine
from installation import clone, process, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

BROKEN_YAML = "key: [unclosed\n"
SECRET = "AKIA" + "Q7XK2M9P4R8T6W3Z"
SKIP = (
    '\n[choices.hygiene]\nskip = ["check-yaml"]\n'
    '\n[choices.secrets]\nskip = ["betterleaks"]\n'
)


def mark_optional(data):
    path = data / "policy.toml"
    text = path.read_text().replace(
        "[capabilities.hygiene.choices]\nexclude = []\n",
        '[capabilities.hygiene]\noptional = ["check-yaml"]\n\n'
        "[capabilities.hygiene.choices]\nexclude = []\nskip = []\n",
    )
    path.write_text(
        text
        + '\n[capabilities.secrets]\noptional = ["betterleaks"]\n\n'
        + "[capabilities.secrets.choices]\nskip = []\n"
    )


@pytest.fixture(scope="module")
def consumers(installed, tmp_path_factory):
    """Return a consumer that skips both hooks, one that keeps them, and the env."""
    origin = tmp_path_factory.mktemp("optional policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", mark_optional)
    env = environment(installed, origin)
    found = {}
    for name, table in (("skipped", SKIP), ("kept", "")):
        root = tmp_path_factory.mktemp(f"{name} consumer ü").resolve()
        with on_machine(installed):
            consumer = clone(root / "origin", root / "consumer")
            installed.cli(
                consumer,
                "configure",
                *("--capability", "hygiene", "--capability", "secrets"),
                *("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1"),
                env=env,
            )
            manifest = consumer / "gateseal.toml"
            manifest.write_text(manifest.read_text() + table)
            installed.cli(consumer, "configure", env=env)
            success(["git", "add", "-A"], consumer)
            success(["git", "commit", "-m", "Adopt the policy"], consumer, env)
            success(["git", "tag", "adopted"], consumer)
        found[name] = consumer
    yield found, env
    for consumer in found.values():
        success(["git", "reset", "-q", "--hard", "adopted"], consumer)


@pytest.fixture
def reset(consumers):
    yield consumers
    for consumer in consumers[0].values():
        success(["git", "reset", "-q", "--hard", "adopted"], consumer)
        success(["git", "clean", "-fdq"], consumer)


@pytest.mark.parametrize(("name", "code"), [("skipped", 0), ("kept", 1)])
def test_commit_run_and_gate_skip_only_a_skipped_hook(installed, reset, name, code):
    found, env = reset
    consumer = found[name]
    (consumer / "broken.yaml").write_text(BROKEN_YAML)
    success(["git", "add", "broken.yaml"], consumer)
    for command in ("run", "gate"):
        result = installed.cli(consumer, command, env=env, code=code)
        hooks = {d.get("hook") for d in result["diagnostics"]}
        assert ("check-yaml" in hooks) == bool(code)
    committed = process(["git", "commit", "-m", "Add broken YAML"], consumer, env)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == code, shown
    assert ("check yaml" in shown) == bool(code), shown


def test_a_skipped_tool_has_no_files_and_no_history_scan(installed, reset):
    found, env = reset
    consumer = found["skipped"]
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["native_artifacts"] == {}
    assert not [path for path in consumer.glob(".gateseal/**/*") if path.is_file()]
    status = installed.cli(consumer, "status", env=env)
    assert "Choice secrets.skip: betterleaks." in {
        d["message"] for d in status["diagnostics"]
    }
    base = success(["git", "rev-parse", "HEAD"], consumer).stdout.strip()
    (consumer / "leak.txt").write_text(f"key = {SECRET}\n")
    success(["git", "add", "leak.txt"], consumer)
    success(["git", "commit", "-q", "-m", "Leak"], consumer, env)
    success(["git", "rm", "-q", "leak.txt"], consumer)
    success(["git", "commit", "-q", "-m", "Remove the leak"], consumer, env)
    head = success(["git", "rev-parse", "HEAD"], consumer).stdout.strip()
    result = installed.cli(consumer, "gate", "--base", base, "--head", head, env=env)
    assert result["code"] == "checks_passed"
    assert not [d for d in result["diagnostics"] if d["category"] == "history"]
