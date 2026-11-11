"""Lock, add and pin the versions a team policy reviews.

The hook repository is a real local repository that its GitHub URL reaches
through url.<base>.insteadOf, so policy lock and add read its tags with Git.
Git's global configuration is a file of each test's own.
"""

import json

import pytest
from gateseal import authoring
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_policy_tree
from team_policy import git

HOOKS = "https://github.com/pre-commit/pre-commit-hooks"
POLICY = """\
schema = 4
release = "team-1"
minimum_compiler = "0.1.0"

[capabilities.hygiene]
tools = ["pre-commit-hooks"]

[tools.pre-commit-hooks]
hook_ids = ["check-yaml"]

[choices.hygiene.exclude]
default = []

[components.pre-commit-hooks]
pin = "6.0.0"

[components.pre-commit-hooks.versions."6.0.0"]
"""
UNRELEASED = '\n[components.pre-commit-hooks.versions."5.5.0"]\n'


@pytest.fixture(scope="module")
def upstream(tmp_path_factory):
    """The hook repository, with an annotated v5.0.0 and a lightweight v6.0.0."""
    repository = tmp_path_factory.mktemp("upstream") / "pre-commit-hooks"
    repository.mkdir()
    git(repository, "init", "-q", "--initial-branch=main")
    commits = {}
    for tag in ("v5.0.0", "v6.0.0"):
        git(repository, "commit", "-q", "--allow-empty", "-m", tag)
        git(repository, "tag", *(["-a", "-m", tag] if tag == "v5.0.0" else []), tag)
        commits[tag] = git(repository, "rev-parse", "HEAD")
    return repository, commits


@pytest.fixture
def offline(upstream, tmp_path, monkeypatch):
    settings = tmp_path / "global.gitconfig"
    settings.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(settings))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_PARAMETERS", raising=False)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{upstream[0].as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", HOOKS)
    return upstream[1]


def team(tmp_path, policy=POLICY):
    root = tmp_path / "policy"
    root.mkdir()
    (root / "policy.toml").write_text(policy)
    return root


def files(root):
    return {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


def check_policy_note(notes, root):
    """Check that notes hold one policy note naming team-1 and root's digest."""
    [note] = notes
    assert (note.severity, note.category) == ("info", "policy")
    named = ("team-1", load_policy_tree(root).content_digest)
    assert all(value in note.message for value in named)


def test_lock_and_add_record_the_tagged_commits(offline, tmp_path):
    root = team(tmp_path)
    locked = authoring.lock(root)
    assert (locked.code, locked.changed) == ("locked", ("policy.lock",))
    check_policy_note(locked.notes, root)
    added = authoring.add(root, "pre-commit-hooks", "5.0.0")
    assert (added.code, added.changed) == ("added", ("policy.toml", "policy.lock"))
    check_policy_note(added.notes, root)
    [step] = added.next_steps
    assert "policy.lock" in step
    assert "gateseal policy pin pre-commit-hooks 5.0.0" in step
    lock = json.loads((root / "policy.lock").read_bytes())
    assert lock["components"]["pre-commit-hooks"]["versions"] == {
        "5.0.0": {"revision": offline["v5.0.0"]},
        "6.0.0": {"revision": offline["v6.0.0"]},
    }


def test_adding_an_unreleased_version_names_the_release_to_check(offline, tmp_path):
    root = team(tmp_path)
    authoring.lock(root)
    before = files(root)
    error = refused(authoring.add, root, "pre-commit-hooks", "7.0.0")
    assert error.code == "upstream_tag_missing"
    assert "gateseal policy add pre-commit-hooks 7.0.0" in error.next_step
    assert files(root) == before


@pytest.mark.parametrize("version", ["5.5.0", "7.0.0"], ids=["it", "another"])
def test_a_reviewed_version_without_a_release_keeps_its_own_step(
    offline, tmp_path, version
):
    root = team(tmp_path, POLICY + UNRELEASED)
    before = files(root)
    error = refused(authoring.add, root, "pre-commit-hooks", version)
    assert error.code == "upstream_tag_missing"
    assert "pre-commit-hooks 5.5.0" in error.next_step
    assert "gateseal policy lock" in error.next_step
    assert "gateseal policy add" not in error.next_step
    assert files(root) == before


def test_a_tag_read_that_fails_names_the_command_to_rerun(offline, tmp_path):
    releases = authoring.Releases("git", tmp_path, "gateseal policy lock")
    assert releases.tagged(HOOKS, "v5.0.0") == offline["v5.0.0"]
    assert releases.tagged(HOOKS, "v9.0.0") is None
    missing = str(tmp_path / "missing")
    error = refused(releases.tagged, missing, "v1.0.0")
    assert (error.code, error.exit_code) == ("upstream_unavailable", 4)
    assert f"git ls-remote {missing}" in error.next_step
    assert "gateseal policy lock" in error.next_step
    assert [path.name for path in tmp_path.iterdir()] == ["global.gitconfig"]


def add_requires(root, version, requires):
    path = root / "policy.toml"
    table = f"[components.{version}]\n"
    path.write_text(path.read_text().replace(table, f"{table}requires = {requires}\n"))


@pytest.fixture
def started(tmp_path):
    root = tmp_path / "policy"
    authoring.init(root)
    return root


def test_pin_refuses_an_unreviewed_version(started):
    before = files(started)
    error = refused(authoring.pin, started, "betterleaks", "1.10.0")
    assert error.code == "version_unreviewed"
    assert "gateseal policy add betterleaks 1.10.0" in error.next_step
    assert files(started) == before


def test_pin_refuses_a_version_that_another_pin_rules_out(started):
    add_requires(started, 'ruff.versions."0.17.1"', '{ betterleaks = "1.9.0" }')
    before = files(started)
    error = refused(authoring.pin, started, "betterleaks", "1.8.0")
    assert error.code == "requires_conflict"
    named = ("ruff 0.17.1", "betterleaks 1.9.0", "betterleaks 1.8.0")
    assert all(value in str(error) for value in named)
    assert all(value in error.next_step for value in ("requires", "policy.toml"))
    assert files(started) == before
