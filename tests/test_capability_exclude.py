"""A policy that exposes exclude for python keeps a path from Ruff everywhere.

A commit, gateseal run and gateseal gate run pre-commit on the same generated
configuration, so all three skip the path a repository excludes and check
every other.
"""

import pytest
from conftest import on_machine
from installation import clone, process, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

UNDEFINED = b"value = undefined_name\n"
"""A Ruff finding that no fix removes."""


def expose_exclude(data):
    path = data / "policy.toml"
    target = 'target_version = "py311"\n'
    path.write_text(path.read_text().replace(target, target + "exclude = []\n"))


@pytest.fixture(scope="module")
def module_excluded(installed, tmp_path_factory):
    origin = tmp_path_factory.mktemp("exclude policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", expose_exclude)
    env = environment(installed, origin)
    root = tmp_path_factory.mktemp("exclude consumer ü").resolve()
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        flags = ("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1")
        installed.cli(consumer, "configure", "--capability", "python", *flags, env=env)
        manifest = consumer / "gateseal.toml"
        manifest.write_text(
            manifest.read_text() + '\n[choices.python]\nexclude = ["vendor"]\n'
        )
        installed.cli(consumer, "configure", env=env)
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-m", "Adopt the policy"], consumer, env)
        success(["git", "tag", "adopted"], consumer)
    return consumer, env


@pytest.fixture
def excluded(module_excluded):
    """The adopted consumer, reset to its adoption after each test."""
    yield module_excluded
    consumer, _ = module_excluded
    success(["git", "reset", "-q", "--hard", "adopted"], consumer)
    success(["git", "clean", "-fdq"], consumer)


@pytest.mark.parametrize(
    ("name", "code"), [("vendor/legacy.py", 0), ("app/legacy.py", 1)]
)
def test_commit_run_and_gate_skip_only_the_excluded_path(
    installed, excluded, name, code
):
    consumer, env = excluded
    path = consumer / name
    path.parent.mkdir(parents=True)
    path.write_bytes(UNDEFINED)
    success(["git", "add", "--", name], consumer)
    for command in ("run", "gate"):
        result = installed.cli(consumer, command, env=env, code=code)
        assert result["command"] == command
    committed = process(["git", "commit", "-m", "Add a module"], consumer, env)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == code, shown
    assert (f"{name}:1:9: F821" in shown) == bool(code), shown
