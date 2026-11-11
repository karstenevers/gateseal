"""Upgrade and roll back the compiler through a verified candidate.

The current compiler installs the candidate wheel, has it configure and check
a scratch repository, and writes what it rendered. installed and
next_installed are two genuine compiler artifacts; the variant changes the
pre-commit and the bundled policy release that a compiler carries.
"""

import hashlib
import json
import sys

import pytest
from conftest import TRACKED, adopt, commit_hygiene, snapshot, uv_shim
from installation import (
    clone,
    next_release,
    package_version,
    success,
    versioned_package,
)
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

CURRENT = package_version()
NEXT = next_release(CURRENT)


def flags(installation):
    return [
        "--compiler-wheel",
        installation.wheel,
        "--compiler-sha256",
        installation.checksum,
    ]


def tracked(root):
    return {name: (root / name).read_bytes() for name in TRACKED}


def targets(result):
    return {
        d["target"]: (d["from"], d["to"], d["state"])
        for d in result["diagnostics"]
        if d["category"] == "upgrade"
    }


def committed_compiler(root):
    lock = success(["git", "show", "HEAD:gateseal.lock"], root).stdout
    return json.loads(lock)["compiler"]["version"]


@pytest.fixture(scope="module")
def variant(tmp_path_factory):
    """Return the wheel and SHA-256 of a release with another runtime and policy.

    It carries pre-commit 4.6.0, CPython 3.14.11 and its own bundled policy
    release. It is never activated, since those hashes are made up.
    """
    directory = tmp_path_factory.mktemp("variant wheel ü")
    package = versioned_package(directory / "package", next_release(NEXT))
    data = package / "src/gateseal/data"
    runtime = data / "runtime.lock"
    text = runtime.read_text().replace("4.7.0", "4.6.0").replace("3.14.10", "3.14.11")
    runtime.write_text(text.replace('"patch": 10,', '"patch": 11,'))
    policy = data / "reference/policy.toml"
    policy.write_text(policy.read_text().replace('release = "', 'release = "variant-'))
    command = ["uv", "build", "--no-config", "--wheel", "--python", sys.executable]
    success([*command, "--out-dir", directory / "dist", package], directory)
    (wheel,) = (directory / "dist").glob("*.whl")
    return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest()


def test_two_repositories_upgrade_and_roll_back_independently(
    installed, next_installed, tmp_path
):
    first = adopt(installed, clone(tmp_path / "first origin", tmp_path / "first ü"))
    second = clone(tmp_path / "second origin", tmp_path / "second ü")
    next_installed.cli(
        second,
        "configure",
        "--capability",
        "hygiene",
        *flags(next_installed),
    )
    manifest = second / "gateseal.toml"
    manifest.write_text(
        manifest.read_text() + '\n[exceptions.pre-commit-hooks]\nversion = "5.0.0"\n'
        'reason = "Pinned for the migration"\nreview_by = 2027-01-31\n'
    )
    next_installed.cli(second, "configure")
    next_installed.cli(second, "activate")
    success(["git", "add", *TRACKED], second)
    success(["git", "commit", "-m", "Adopt the next compiler"], second)
    other = (snapshot(second), tracked(second))
    prepared = snapshot(first)

    upgraded = installed.cli(first, "upgrade", *flags(next_installed), "--activate")
    assert upgraded["code"] == "upgraded"
    assert targets(upgraded)["compiler"] == (CURRENT, NEXT, "upgrade")
    assert targets(upgraded)["pre-commit-hooks"] == ("6.0.0", "6.0.0", "unchanged")
    assert upgraded["changed"][0] == "gateseal.lock"
    assert snapshot(first) != prepared
    success(["git", "add", *TRACKED], first)
    commit_hygiene(first)
    assert committed_compiler(first) == NEXT
    assert next_installed.cli(first, "doctor")["code"] == "healthy"
    assert (
        installed.cli(first, "status", code=2)["code"] == "selected_compiler_required"
    )
    assert (snapshot(second), tracked(second)) == other

    back = next_installed.cli(first, "upgrade", *flags(installed), "--activate")
    assert targets(back)["compiler"] == (NEXT, CURRENT, "rollback")
    success(["git", "add", *TRACKED], first)
    commit_hygiene(first)
    assert committed_compiler(first) == CURRENT
    assert installed.cli(first, "status")["code"] == "recorded"
    assert (snapshot(second), tracked(second)) == other
    commit_hygiene(second)
    assert committed_compiler(second) == NEXT


def test_bundled_policy_and_runtime_move_only_when_asked(installed, variant, consumer):
    wheel, checksum = variant
    installed.cli(consumer, "configure", "--capability", "hygiene", *flags(installed))
    before = tracked(consumer)
    candidate = ["--compiler-wheel", wheel, "--compiler-sha256", checksum]
    refused = installed.cli(consumer, "upgrade", *candidate, code=2)
    assert refused["code"] == "bundled_policy_moves"
    assert "--policy bundled" in refused["next_steps"][0]
    assert tracked(consumer) == before

    moved = installed.cli(consumer, "upgrade", *candidate, "--policy", "bundled")
    assert sorted(moved["changed"]) == sorted(TRACKED)
    assert targets(moved)["pre-commit"] == ("4.7.0", "4.6.0", "rollback")
    assert targets(moved)["python"][2] == "upgrade"
    assert targets(moved)["policy"][2] == "change"
    assert 'release = "variant-' in (consumer / "gateseal.toml").read_text()
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["runtime"]["pre_commit"]["version"] == "4.6.0"
    config = json.loads((consumer / ".pre-commit-config.yaml").read_bytes())
    assert config["minimum_pre_commit_version"] == "4.6.0"
    install = "Install the command of gateseal"
    assert any(install in step for step in moved["next_steps"])


def test_candidate_must_take_every_existing_pin(installed, next_installed, tmp_path):
    """Refuse a rollback below the policy's minimum compiler, keeping the policy."""
    origin = tmp_path / "policy" / "gateseal-policy"
    origin.mkdir(parents=True)
    git(origin, "init", "--initial-branch=main")

    def newer(data):
        text = (data / "policy.toml").read_text()
        (data / "policy.toml").write_text(
            text.replace('minimum_compiler = "0.1.0"', f'minimum_compiler = "{NEXT}"')
        )

    publish(origin, "v1", newer)
    env = environment(next_installed, origin)
    consumer = clone(tmp_path / "origin", tmp_path / "consumer ü")
    next_installed.cli(
        consumer,
        "configure",
        "--capability",
        "hygiene",
        "--policy",
        URL,
        "--policy-path",
        "policy",
        "--policy-tag",
        "v1",
        *flags(next_installed),
        env=env,
    )
    before = tracked(consumer)
    refused = next_installed.cli(
        consumer, "upgrade", *flags(installed), code=2, env=env
    )
    assert refused["code"] == "candidate_rejected"
    message = refused["diagnostics"][0]["message"]
    assert f"gateseal {CURRENT} configure stopped with incompatible_compiler" in message
    assert tracked(consumer) == before
    combined = next_installed.cli(
        consumer, "upgrade", *flags(installed), "pre-commit-hooks", code=2, env=env
    )
    assert combined["code"] == "invalid_invocation"


def test_corrupt_or_unavailable_candidates_write_nothing(
    installed, next_installed, configured, tmp_path
):
    before = tracked(configured)
    wrong = installed.cli(
        configured,
        "upgrade",
        "--compiler-wheel",
        next_installed.wheel,
        "--compiler-sha256",
        installed.checksum,
        code=4,
    )
    assert wrong["code"] == "artifact_hash_mismatch"
    missing = tmp_path / f"gateseal-{NEXT}-py3-none-any.whl"
    gone = installed.cli(
        configured,
        "upgrade",
        "--compiler-wheel",
        missing,
        "--compiler-sha256",
        installed.checksum,
        code=4,
    )
    assert gone["code"] == "missing_artifact"
    broken = tmp_path / "broken" / f"gateseal-{NEXT}-py3-none-any.whl"
    broken.parent.mkdir()
    broken.write_bytes(b"not a wheel")
    digest = hashlib.sha256(b"not a wheel").hexdigest()
    corrupt = installed.cli(
        configured,
        "upgrade",
        "--compiler-wheel",
        broken,
        "--compiler-sha256",
        digest,
        code=2,
    )
    assert corrupt["code"] == "invalid_artifact"
    renamed = tmp_path / "gateseal-candidate.whl"
    renamed.write_bytes(next_installed.wheel.read_bytes())
    named = installed.cli(
        configured,
        "upgrade",
        "--compiler-wheel",
        renamed,
        "--compiler-sha256",
        next_installed.checksum,
        code=2,
    )
    assert named["code"] == "invalid_artifact"
    assert tracked(configured) == before


def test_activation_failure_keeps_the_compiler_upgrade(
    installed, next_installed, configured, tmp_path
):
    failing = uv_shim(installed, tmp_path, "exit 7")
    result = installed.cli(
        configured, "upgrade", *flags(next_installed), "--activate", code=4, env=failing
    )
    assert result["code"] == "activation_failed"
    assert f"gateseal {NEXT}" in result["diagnostics"][0]["message"]
    assert any(NEXT in step for step in result["next_steps"])
    assert next_installed.cli(configured, "check")["code"] == "consistent"
    assert next_installed.cli(configured, "status")["code"] == "needs_activation"
    assert not (configured / ".git/gateseal/current").exists()


def test_candidate_fetches_with_the_worktrees_path_scoped_git_config(
    installed, next_installed, tmp_path
):
    """The policy URL rewrite applies only inside the consumer's Git directory."""
    origin = tmp_path / "policy" / "gateseal-policy"
    origin.mkdir(parents=True)
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1")
    consumer = clone(tmp_path / "origin", tmp_path / "consumer ü").resolve()
    scoped = tmp_path / "scoped.gitconfig"
    scoped.write_text(f'[url "{origin.as_uri()}"]\n\tinsteadOf = {URL}\n')
    settings = tmp_path / "global.gitconfig"
    settings.write_text(f'[includeIf "gitdir:{consumer}/.git/"]\n\tpath = {scoped}\n')
    env = dict(installed.environment, GIT_CONFIG_GLOBAL=str(settings))
    installed.cli(
        consumer,
        "configure",
        "--capability",
        "hygiene",
        "--policy",
        URL,
        "--policy-path",
        "policy",
        "--policy-tag",
        "v1",
        *flags(installed),
        env=env,
    )
    moved = installed.cli(consumer, "upgrade", *flags(next_installed), env=env)
    assert targets(moved)["compiler"] == (CURRENT, NEXT, "upgrade")
    assert next_installed.cli(consumer, "check", env=env)["code"] == "consistent"
    assert not list((consumer / ".git/gateseal").glob(".gateseal-*"))
