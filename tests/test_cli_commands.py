"""Dispatch each repository command through gateseal.cli.main in-process.

No command installs anything: the worktrees are configured and gateseal setup
never runs, so a command that needs pre-commit stops with needs_setup.
"""

import contextlib
import io
import json
import os

import pytest
from conftest import PIN, TRACKED, git_path, named_flags, with_git_config
from gateseal.cli import main
from installation import clone, success
from team_policy import URL, publish


def gateseal(root, *arguments):
    """Return main's exit code and JSON result for a command in root."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), "--json", *map(str, arguments)])
    return code, json.loads(output.getvalue())


def message(result):
    return result["diagnostics"][0]["message"]


def configure(root, *flags):
    code, result = gateseal(root, "configure", "--capability", "hygiene", *flags)
    assert (code, result["code"]) == (0, "configured"), result
    return result


def commit(root, text):
    success(["git", "add", "-A"], root)
    success(["git", "commit", "--quiet", "-m", text], root)


def tracked(root):
    return {name: (root / name).read_bytes() for name in TRACKED}


@pytest.fixture(scope="module")
def adopted(tmp_path_factory):
    """A consumer that committed a bundled configuration, on a machine without setup."""
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
    pin = json.loads((consumer / "gateseal.lock").read_bytes())["gateseal"]
    assert pin == PIN.document()
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
    )
    assert (code, result["code"]) == (2, "invalid_invocation")
    assert named_flags(message(result)) == {
        "--policy-commit",
        "--policy-path",
        "--policy-tag",
        "--policy",
    }
    assert not (unconfigured / "gateseal.toml").exists()


@pytest.mark.parametrize("command", ["configure", "upgrade"])
def test_gateseal_pins_itself_without_flags(unconfigured, command):
    code, result = gateseal(unconfigured, command, "--compiler-sha256", "5" * 64)
    assert (code, result["code"]) == (2, "invalid_invocation")
    assert "--compiler-sha256" in message(result)
    assert not (unconfigured / "gateseal.toml").exists()


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


def checks(result):
    """Return each check's status from a status or doctor result."""
    return {
        entry["check"]: entry["status"]
        for entry in result["diagnostics"]
        if entry["category"] == "check"
    }


@pytest.mark.parametrize("command,exit_code", [("status", 0), ("doctor", 3)])
def test_status_and_doctor_report_the_same_checks_before_setup(
    adopted, command, exit_code
):
    code, result = gateseal(adopted, command)
    assert (code, result["command"], result["code"]) == (
        exit_code,
        command,
        "unhealthy",
    )
    assert message(result) == "2 of 4 checks failed."
    assert checks(result) == {
        "lock": "passed",
        "pre-commit": "failed",
        "launcher": "failed",
        "mise": "skipped",
    }
    assert result["next_steps"] == ["gateseal setup", "gateseal setup --refresh"]


def test_run_before_setup_names_setup(adopted):
    code, result = gateseal(adopted, "run")
    assert (code, result["command"], result["code"]) == (3, "run", "needs_setup")
    assert result["next_steps"] == ["gateseal setup"]


def test_check_agrees_before_setup(adopted):
    code, result = gateseal(adopted, "check")
    assert (code, result["command"], result["code"]) == (0, "check", "consistent")


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
    ],
    ids=["bundled with a tag", "commit and path"],
)
def test_upgrade_targets_reach_their_checks(
    adopted, arguments, exit_code, expected, named
):
    before = tracked(adopted)
    code, result = gateseal(adopted, "upgrade", *arguments)
    assert (code, result["command"], result["code"]) == (exit_code, "upgrade", expected)
    assert named_flags(message(result)) == named
    assert tracked(adopted) == before


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


def test_status_of_an_unfetched_policy_names_check_without_fetching(unfetched):
    import tomllib

    code, result = gateseal(unfetched, "status")
    assert (code, result["command"], result["code"]) == (0, "status", "unhealthy")
    assert checks(result)["lock"] == "failed"
    manifest = tomllib.loads((unfetched / "gateseal.toml").read_text())
    [lock] = [e for e in result["diagnostics"] if e.get("check") == "lock"]
    assert manifest["policy"]["commit"] in lock["message"]
    assert result["next_steps"][0] == "gateseal check"
    assert stored(unfetched) == []


def test_doctor_fetches_the_locked_policy_and_warns_about_a_hooks_path(unfetched):
    success(["git", "config", "core.hooksPath", "custom-hooks"], unfetched)
    code, result = gateseal(unfetched, "doctor")
    assert (code, result["command"], result["code"]) == (3, "doctor", "unhealthy")
    assert checks(result)["lock"] == "passed"
    assert len(stored(unfetched)) == 1
    [warning] = [e for e in result["diagnostics"] if e["severity"] == "warning"]
    assert warning["category"] == "custom_hooks_path"
    assert all(v in warning["message"] for v in ("core.hooksPath", "custom-hooks"))
    assert result["next_steps"] == [
        "gateseal setup",
        "gateseal setup --refresh",
        "Remove the core.hooksPath setting.",
    ]


def test_run_fetches_the_locked_policy_before_it_starts_pre_commit(unfetched):
    code, result = gateseal(unfetched, "run")
    assert (code, result["code"]) == (3, "needs_setup")
    assert len(stored(unfetched)) == 1


def test_check_fetches_the_locked_policy(unfetched):
    code, result = gateseal(unfetched, "check")
    assert (code, result["command"], result["code"]) == (0, "check", "consistent")
    assert len(stored(unfetched)) == 1
