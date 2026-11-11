"""Dispatch each repository command through gateseal.cli.main in-process.

Every command stops before activation would build a uv environment: the
worktrees are configured but never prepared, and a compiler wheel that does
not exist, or a pre-commit hook that gateseal does not own, stops activation
before it installs anything.
"""

import contextlib
import io
import json
import os

import pytest
from conftest import TRACKED, git_path, named_flags, with_git_config
from gateseal.cli import main
from installation import clone, package_version, success
from team_policy import URL, publish

WHEEL = f"gateseal-{package_version()}-py3-none-any.whl"
SOURCE = (
    f"https://raw.githubusercontent.com/example/wheels/{'e' * 40}/artifacts/{WHEEL}"
)
SHA = "5" * 64


def gateseal(root, *arguments):
    """Return main's exit code and JSON result for a command in root."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), "--json", *map(str, arguments)])
    return code, json.loads(output.getvalue())


def message(result):
    return result["diagnostics"][0]["message"]


def configure(root, *flags):
    code, result = gateseal(
        root,
        "configure",
        "--capability",
        "hygiene",
        "--compiler-source",
        SOURCE,
        "--compiler-sha256",
        SHA,
        *flags,
    )
    assert (code, result["code"]) == (0, "configured"), result
    return result


def commit(root, text):
    success(["git", "add", "-A"], root)
    success(["git", "commit", "--quiet", "-m", text], root)


def tracked(root):
    return {name: (root / name).read_bytes() for name in TRACKED}


@pytest.fixture(scope="module")
def adopted(tmp_path_factory):
    """A consumer that committed a bundled configuration and never activated."""
    root = tmp_path_factory.mktemp("cli consumer")
    consumer = clone(root / "origin", root / "consumer")
    configure(consumer)
    commit(consumer, "Adopt hygiene")
    return consumer


@pytest.fixture(scope="module")
def unconfigured(tmp_path_factory):
    root = tmp_path_factory.mktemp("cli unconfigured")
    return clone(root / "origin", root / "consumer")


@pytest.fixture
def configured(adopted, tmp_path):
    """A clone of adopted that a test may change."""
    root = tmp_path / "configured"
    success(["git", "clone", "--quiet", adopted, root], tmp_path)
    return root


def test_configure_writes_what_its_flags_select(consumer):
    result = configure(consumer, "--policy", "bundled", "--ci", "github")
    workflow = ".github/workflows/gateseal.yml"
    assert set(result["changed"]) == {*TRACKED, workflow}
    compiler = json.loads((consumer / "gateseal.lock").read_bytes())["compiler"]
    assert compiler["source"] == {"kind": "https-wheel", "location": SOURCE}
    assert compiler["sha256"] == SHA
    manifest = (consumer / "gateseal.toml").read_text()
    assert 'source = "bundled"' in manifest
    assert '[ci]\nprovider = "github"' in manifest
    assert (consumer / workflow).is_file()


def test_git_policy_flags_without_a_repository_write_nothing(unconfigured):
    code, result = gateseal(
        unconfigured,
        "configure",
        "--capability",
        "hygiene",
        "--policy-commit",
        "0" * 40,
        "--policy-path",
        "policy",
        "--policy-tag",
        "v1",
        "--compiler-source",
        SOURCE,
        "--compiler-sha256",
        SHA,
    )
    assert (code, result["code"]) == (2, "invalid_invocation")
    assert named_flags(message(result)) == {
        "--policy-commit",
        "--policy-path",
        "--policy-tag",
        "--policy",
    }
    assert not (unconfigured / "gateseal.toml").exists()


def test_configure_reads_the_local_compiler_wheel(unconfigured, tmp_path):
    wheel = tmp_path / WHEEL
    code, result = gateseal(
        unconfigured,
        "configure",
        "--capability",
        "hygiene",
        "--compiler-wheel",
        wheel,
        "--compiler-sha256",
        SHA,
    )
    assert (code, result["code"]) == (4, "missing_artifact")
    assert str(wheel) in message(result)


def test_before_configure_status_points_to_configure_and_check_fails(unconfigured):
    code, result = gateseal(unconfigured, "status")
    assert (code, result["code"]) == (0, "unconfigured")
    assert result["next_steps"] == ["gateseal configure --help"]
    code, result = gateseal(unconfigured, "check")
    assert (code, result["command"], result["code"]) == (
        2,
        "check",
        "missing_configuration",
    )
    assert "gateseal configure" in message(result)


@pytest.mark.parametrize(
    "command,exit_code", [("status", 0), ("doctor", 3), ("run", 3)]
)
def test_an_unprepared_worktree_needs_activation(adopted, command, exit_code):
    code, result = gateseal(adopted, command)
    assert (code, result["command"]) == (exit_code, command)
    assert result["code"] == "needs_activation"
    assert result["next_steps"][0] == "gateseal activate"


def test_check_agrees_without_a_preparation(adopted):
    code, result = gateseal(adopted, "check")
    assert (code, result["command"], result["code"]) == (0, "check", "consistent")


def test_activate_in_a_fresh_clone_checks_the_wheel_it_names(adopted, tmp_path):
    fresh = tmp_path / "fresh"
    success(["git", "clone", "--quiet", adopted, fresh], tmp_path)
    wheel = tmp_path / WHEEL
    code, result = gateseal(fresh, "activate", "--compiler-wheel", wheel)
    assert (code, result["command"], result["code"]) == (
        4,
        "activate",
        "missing_artifact",
    )
    assert str(wheel) in message(result)
    assert git_path(fresh, "gateseal").is_dir()


def test_upgrade_without_targets_changes_nothing(adopted):
    before = tracked(adopted)
    code, result = gateseal(adopted, "upgrade")
    assert (code, result["command"], result["code"]) == (0, "upgrade", "unchanged")
    assert "already selected" in message(result)
    assert tracked(adopted) == before


def test_upgrade_records_a_version_exception(configured):
    code, result = gateseal(
        configured,
        "upgrade",
        "pre-commit-hooks=5.0.0",
        "--reason",
        "Upstream issue 123",
        "--review-by",
        "2026-12-31",
    )
    assert (code, result["code"]) == (0, "upgraded")
    assert "gateseal.toml" in result["changed"]
    assert (
        "[exceptions.pre-commit-hooks]\n"
        'version = "5.0.0"\n'
        'reason = "Upstream issue 123"\n'
        "review_by = 2026-12-31\n"
    ) in (configured / "gateseal.toml").read_text()


@pytest.mark.parametrize(
    "arguments,exit_code,expected,named",
    [
        (
            ["--policy", "bundled", "--policy-tag", "v1"],
            2,
            "invalid_invocation",
            {"--policy-commit", "--policy-path", "--policy-tag", "--policy"},
        ),
        (
            ["--policy-commit", "0" * 40, "--policy-path", "policy"],
            2,
            "invalid_invocation",
            {"--policy", "--policy-tag"},
        ),
        (
            ["--compiler-wheel", "{wheel}", "--compiler-sha256", SHA],
            4,
            "missing_artifact",
            {"{wheel}"},
        ),
        (
            ["--compiler-source", SOURCE],
            2,
            "missing_artifact",
            {"--compiler-wheel", "--compiler-source", "--compiler-sha256"},
        ),
    ],
    ids=["bundled with a tag", "commit and path", "wheel", "source alone"],
)
def test_upgrade_targets_reach_their_checks(
    adopted, tmp_path, arguments, exit_code, expected, named
):
    wheel = tmp_path / WHEEL
    before = tracked(adopted)
    filled = [argument.format(wheel=wheel) for argument in arguments]
    code, result = gateseal(adopted, "upgrade", *filled)
    assert (code, result["command"], result["code"]) == (exit_code, "upgrade", expected)
    shown = message(result)
    assert all(token.format(wheel=wheel) in shown for token in named)
    assert named_flags(shown) == {token for token in named if token.startswith("--")}
    assert tracked(adopted) == before


def test_upgrade_reports_an_activation_that_cannot_start(configured):
    hook = git_path(configured, "hooks/pre-commit")
    hook.parent.mkdir(exist_ok=True)
    hook.write_bytes(b"#!/bin/sh\nexit 0\n")
    code, result = gateseal(configured, "upgrade", "--activate")
    assert (code, result["code"]) == (2, "activation_failed")
    assert "unmanaged_hook" in message(result)
    assert hook.read_bytes() == b"#!/bin/sh\nexit 0\n"


def map_team_url(monkeypatch, origin):
    """Point the team policy's URL at origin through Git's own configuration."""
    settings = {f"url.{origin.as_uri()}.insteadOf": URL}
    for key, value in with_git_config(dict(os.environ), settings).items():
        if os.environ.get(key) != value:
            monkeypatch.setenv(key, value)


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """A team policy origin and a consumer that committed its v1 release."""
    root = tmp_path_factory.mktemp("cli team policy")
    origin = root / "gateseal-policy"
    origin.mkdir()
    success(["git", "init", "--quiet", "--initial-branch=main"], origin)
    publish(origin, "v1")
    consumer = clone(root / "consumer origin", root / "consumer")
    with pytest.MonkeyPatch.context() as monkeypatch:
        map_team_url(monkeypatch, origin)
        configure(
            consumer, "--policy", URL, "--policy-path", "policy", "--policy-tag", "v1"
        )
    commit(consumer, "Adopt the team policy")
    return origin, consumer


@pytest.fixture
def unfetched(team, tmp_path, monkeypatch):
    """A fresh clone of the team consumer, which has stored no policy release."""
    origin, consumer = team
    map_team_url(monkeypatch, origin)
    fresh = tmp_path / "fresh"
    success(["git", "clone", "--quiet", consumer, fresh], tmp_path)
    return fresh


def stored(root):
    policies = git_path(root, "gateseal") / "policies"
    return sorted(policies.iterdir()) if policies.exists() else []


def test_status_of_an_unfetched_policy_needs_activation_without_fetching(unfetched):
    import tomllib

    code, result = gateseal(unfetched, "status")
    assert (code, result["command"], result["code"]) == (
        0,
        "status",
        "needs_activation",
    )
    manifest = tomllib.loads((unfetched / "gateseal.toml").read_text())
    assert manifest["policy"]["commit"] in message(result)
    assert result["next_steps"] == ["gateseal activate"]
    assert stored(unfetched) == []


@pytest.mark.parametrize("conflict", ["custom_hooks_path", "unmanaged_hook"])
def test_status_names_a_hook_conflict_before_the_policy_is_fetched(unfetched, conflict):
    if conflict == "custom_hooks_path":
        success(["git", "config", "core.hooksPath", "custom-hooks"], unfetched)
    else:
        hook = git_path(unfetched, "hooks/pre-commit")
        hook.parent.mkdir(exist_ok=True)
        hook.write_bytes(b"#!/bin/sh\nexit 0\n")
    code, result = gateseal(unfetched, "status")
    assert (code, result["command"], result["code"]) == (0, "status", conflict)
    (step,) = result["next_steps"]
    assert "gateseal activate" in step
    assert stored(unfetched) == []


def test_doctor_of_an_unfetched_policy_names_the_missing_release(unfetched):
    code, result = gateseal(unfetched, "doctor")
    assert (code, result["command"], result["code"]) == (
        3,
        "doctor",
        "policy_unavailable",
    )
    assert result["next_steps"] == ["gateseal activate"]
    assert stored(unfetched) == []


def test_check_fetches_the_locked_policy(unfetched):
    code, result = gateseal(unfetched, "check")
    assert (code, result["command"], result["code"]) == (0, "check", "consistent")
    assert len(stored(unfetched)) == 1


def test_activate_fetches_the_policy_before_it_checks_the_wheel(unfetched, tmp_path):
    wheel = tmp_path / WHEEL
    code, result = gateseal(unfetched, "activate", "--compiler-wheel", wheel)
    assert (code, result["command"], result["code"]) == (
        4,
        "activate",
        "missing_artifact",
    )
    assert len(stored(unfetched)) == 1
