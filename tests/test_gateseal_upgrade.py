"""Move a repository to another gateseal release with gateseal upgrade.

The gateseal that runs the upgrade writes its own pin. Of the old Lock it
reads only the ownership ledger, so a newer and an older release move a
repository both ways. installed and next_installed are two genuine releases
whose tags gateseal_origin holds.
"""

import dataclasses
import json

import pytest
from conftest import BUNDLED_MINIMUM, TRACKED, adopt, commit_hygiene
from gateseal.compiler_release import REPOSITORY
from installation import (
    clone,
    install,
    next_release,
    package_version,
    success,
    tag_release,
    versioned_package,
)
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

CURRENT = package_version()
NEXT = next_release(CURRENT)


def tracked(root):
    return {name: (root / name).read_bytes() for name in TRACKED}


def targets(result):
    return {
        d["target"]: (d["from"], d["to"], d["state"])
        for d in result["diagnostics"]
        if d["category"] == "upgrade"
    }


def release(origin, version):
    """Return how the upgrade report names version: with its tag's commit."""
    commit = git(origin, "rev-parse", f"v{version}^{{commit}}")
    return f"{version} at {commit}"


def committed_pin(root):
    lock = success(["git", "show", "HEAD:gateseal.lock"], root).stdout
    return json.loads(lock)["gateseal"]


def hook_commit(root):
    """Return the commit at which the native configuration runs gateseal's hook."""
    config = json.loads((root / ".pre-commit-config.yaml").read_bytes())
    first = config["repos"][0]
    assert (first["repo"], first["hooks"]) == (REPOSITORY, [{"id": "gateseal-check"}])
    return first["rev"]


def test_two_repositories_upgrade_and_roll_back_independently(
    installed, next_installed, gateseal_origin, tmp_path
):
    first = adopt(installed, clone(tmp_path / "first origin", tmp_path / "first ü"))
    second = clone(tmp_path / "second origin", tmp_path / "second ü")
    next_installed.cli(second, "configure", "--capability", "hygiene")
    manifest = second / "gateseal.toml"
    manifest.write_text(
        manifest.read_text() + '\n[exceptions.pre-commit-hooks]\nversion = "5.0.0"\n'
        'reason = "Pinned for the migration"\nreview_by = 2027-01-31\n'
    )
    next_installed.cli(second, "configure")
    success(["git", "add", *TRACKED], second)
    success(["git", "commit", "-m", "Adopt the next gateseal"], second)
    other = tracked(second)
    current, upcoming = (release(gateseal_origin, v) for v in (CURRENT, NEXT))

    upgraded = next_installed.cli(first, "upgrade")
    assert upgraded["code"] == "upgraded"
    assert targets(upgraded) == {"gateseal": (current, upcoming, "upgrade")}
    assert upgraded["changed"][0] == "gateseal.lock"
    setup = f"uvx gateseal@{NEXT} setup"
    assert any(setup in step for step in upgraded["next_steps"])
    success(["git", "add", *TRACKED], first)
    commit_hygiene(first)
    assert committed_pin(first)["version"] == NEXT
    assert hook_commit(first) == committed_pin(first)["commit"] == upcoming[-40:]
    assert next_installed.cli(first, "status")["code"] == "healthy"
    refused = installed.cli(first, "check", code=2)
    assert refused["code"] == "selected_compiler_required"
    assert "gateseal upgrade" in refused["next_steps"][0]
    assert tracked(second) == other

    back = installed.cli(first, "upgrade")
    assert targets(back) == {"gateseal": (upcoming, current, "rollback")}
    success(["git", "add", *TRACKED], first)
    commit_hygiene(first)
    assert committed_pin(first)["version"] == CURRENT
    assert hook_commit(first) == committed_pin(first)["commit"] == current[-40:]
    assert installed.cli(first, "status")["code"] == "healthy"
    assert tracked(second) == other
    commit_hygiene(second)
    assert committed_pin(second)["version"] == NEXT


@pytest.fixture(scope="module")
def variant(installed, gateseal_origin, tmp_path_factory):
    """Install a later release that bundles another policy release, and tag it."""
    directory = tmp_path_factory.mktemp("variant release ü").resolve()
    version = next_release(NEXT)
    package = versioned_package(directory / "package", version)
    policy = package / "src/gateseal/data/reference/policy.toml"
    policy.write_text(policy.read_text().replace('release = "', 'release = "variant-'))
    tag_release(gateseal_origin, version, package)
    return dataclasses.replace(
        install(directory, package), environment=installed.environment
    )


def test_the_bundled_policy_moves_only_when_asked(installed, variant, consumer):
    installed.cli(consumer, "configure", "--capability", "hygiene")
    before = tracked(consumer)
    refused = variant.cli(consumer, "upgrade", code=2)
    assert refused["code"] == "bundled_policy_moves"
    assert "--policy bundled" in refused["next_steps"][0]
    assert tracked(consumer) == before

    combined = variant.cli(consumer, "upgrade", "pre-commit-hooks", code=2)
    assert combined["code"] == "invalid_invocation"

    moved = variant.cli(consumer, "upgrade", "--policy", "bundled")
    assert sorted(moved["changed"]) == [
        ".pre-commit-config.yaml",
        "gateseal.lock",
        "gateseal.toml",
    ]
    assert list(targets(moved)) == ["gateseal"]
    assert 'release = "variant-' in (consumer / "gateseal.toml").read_text()
    assert variant.cli(consumer, "check")["code"] == "consistent"


def test_an_older_gateseal_cannot_move_below_the_policys_minimum(
    installed, next_installed, tmp_path
):
    origin = tmp_path / "policy" / "gateseal-policy"
    origin.mkdir(parents=True)
    git(origin, "init", "--initial-branch=main")

    def newer(data):
        text = (data / "policy.toml").read_text()
        assert BUNDLED_MINIMUM in text
        (data / "policy.toml").write_text(
            text.replace(BUNDLED_MINIMUM, f'minimum_compiler = "{NEXT}"')
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
        env=env,
    )
    before = tracked(consumer)
    refused = installed.cli(consumer, "upgrade", code=2, env=env)
    assert refused["code"] == "incompatible_compiler"
    assert tracked(consumer) == before


def test_a_move_fetches_with_the_worktrees_path_scoped_git_config(
    installed, next_installed, gateseal_origin, tmp_path
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
    home = installed.environment["GIT_CONFIG_GLOBAL"]
    settings.write_text(
        f"[include]\n\tpath = {home}\n"
        f'[includeIf "gitdir:{consumer}/.git/"]\n\tpath = {scoped}\n'
    )
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
        env=env,
    )
    moved = next_installed.cli(consumer, "upgrade", env=env)
    current, upcoming = (release(gateseal_origin, v) for v in (CURRENT, NEXT))
    assert targets(moved)["gateseal"] == (current, upcoming, "upgrade")
    assert next_installed.cli(consumer, "check", env=env)["code"] == "consistent"
    assert not list((consumer / ".git/gateseal").glob(".gateseal-*"))


FORMAT_1_STEP = (
    "Delete gateseal.lock and the files gateseal wrote (.pre-commit-config.yaml, "
    ".gateseal/ and .github/workflows/gateseal.yml), then run gateseal configure."
)


def test_every_command_refuses_a_lock_of_format_1(installed, configured):
    """gateseal 0.1.0 wrote format 1, and no command reads it."""
    lock = configured / "gateseal.lock"
    document = json.loads(lock.read_bytes())
    del document["gateseal"]
    document.update(
        schema=1,
        compiler={"name": "gateseal", "version": "0.1.0", "sha256": "0" * 64},
        runtime={"minimum_uv": "0.9.0"},
    )
    lock.write_text(json.dumps(document))
    before = tracked(configured)
    for command in ("configure", "check", "run", "gate", "upgrade"):
        refused = installed.cli(configured, command, code=2)
        assert (refused["code"], refused["next_steps"]) == (
            "unsupported_schema",
            [FORMAT_1_STEP],
        )
    assert FORMAT_1_STEP in installed.cli(configured, "status", code=3)["next_steps"]
    assert tracked(configured) == before
