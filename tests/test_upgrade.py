"""Upgrade and roll back a team policy and its components with the installed command.

Each release is the bundled reference with other pins, reviewed versions or
requirements, so its Policy Lock still holds every fact it needs offline.
"""

import json
import shutil

import pytest
from conftest import TRACKED, commit_hygiene, unreachable, uv_shim
from gateseal.validation import canonical
from installation import PACKAGE, clone, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

REASON = ["--reason", "Our fixtures need the 1.9.0 rules", "--review-by", "2027-01-31"]


def pinned(hooks, betterleaks):
    def change(data):
        text = (data / "policy.toml").read_text()
        text = text.replace('pin = "6.0.0"', f'pin = "{hooks}"')
        (data / "policy.toml").write_text(
            text.replace('pin = "1.9.0"', f'pin = "{betterleaks}"')
        )

    return change


def without_betterleaks_1_8(data):
    toml = data / "policy.toml"
    toml.write_text(
        toml.read_text().replace('[components.betterleaks.versions."1.8.0"]\n', "")
    )
    lock = json.loads((data / "policy.lock").read_bytes())
    del lock["components"]["betterleaks"]["versions"]["1.8.0"]
    (data / "policy.lock").write_bytes(canonical(lock))


def requiring_hooks_6(data):
    toml = data / "policy.toml"
    toml.write_text(
        toml.read_text().replace(
            '[components.betterleaks.versions."1.9.0"]\n',
            '[components.betterleaks.versions."1.9.0"]\n'
            'requires = { pre-commit-hooks = "6.0.0" }\n',
        )
    )


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """Publish the releases v1, v2 and v3 of a team policy.

    v1 has older pins, v2's Betterleaks needs pre-commit-hooks 6.0.0, and v3 no
    longer reviews Betterleaks 1.8.0.
    """
    origin = tmp_path_factory.mktemp("upgrade policy ü") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    commits = {
        "v1": publish(origin, "v1", pinned("5.0.0", "1.8.0")),
        "v2": publish(origin, "v2", requiring_hooks_6),
        "v3": publish(origin, "v3", without_betterleaks_1_8),
    }
    return origin, commits


def tracked(root):
    names = [*TRACKED, ".gateseal/betterleaks.toml"]
    return {
        name: (root / name).read_bytes() for name in names if (root / name).exists()
    }


def upgrade(installed, root, env, *arguments, code=0):
    return installed.cli(root, "upgrade", *arguments, code=code, env=env)


def targets(result):
    return {
        d["target"]: (d["from"], d["to"], d["state"])
        for d in result["diagnostics"]
        if d["category"] == "upgrade"
    }


def exceptions(result):
    return {
        d["component"]: (d["version"], d["status"])
        for d in result["diagnostics"]
        if d["category"] == "exception"
    }


def consumer_at(installed, team, root, tag, *capabilities):
    origin, _ = team
    env = environment(installed, origin)
    consumer = clone(root / "origin", root / "consumer ü")
    flags = [f for name in ("hygiene", *capabilities) for f in ("--capability", name)]
    installed.cli(
        consumer,
        "configure",
        *flags,
        "--policy",
        URL,
        "--policy-path",
        "policy",
        "--policy-tag",
        tag,
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
        env=env,
    )
    return consumer, env


def test_policy_upgrade_and_rollback_keep_choices_and_exceptions(
    installed, team, tmp_path
):
    _, commits = team
    consumer, env = consumer_at(installed, team, tmp_path, "v1", "secrets")
    manifest = consumer / "gateseal.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "[choices]", '[choices.hygiene]\nexclude = ["vendor"]  # generated'
        )
    )
    installed.cli(consumer, "configure", env=env)
    newer = upgrade(installed, consumer, env, "betterleaks=1.9.0", *REASON)
    assert newer["code"] == "upgraded"
    assert targets(newer)["betterleaks"] == ("1.8.0", "1.9.0", "upgrade")
    assert exceptions(newer) == {"betterleaks": ("1.9.0", "added")}
    before = tracked(consumer)

    moved = upgrade(installed, consumer, env, "--policy-tag", "v2")
    assert sorted(moved["changed"]) == [
        ".pre-commit-config.yaml",
        "gateseal.lock",
        "gateseal.toml",
    ]
    found = {d["category"]: d for d in moved["diagnostics"]}
    assert (
        found["policy_tag"]["message"]
        == f"Tag v2 names commit {commits['v2']}, as adoption requires."
    )
    assert targets(moved)["pre-commit-hooks"] == ("5.0.0", "6.0.0", "upgrade")
    assert targets(moved)["betterleaks"] == ("1.9.0", "1.9.0", "unchanged")
    assert targets(moved)["pre-commit"][2] == "unchanged"
    assert exceptions(moved) == {"betterleaks": ("1.9.0", "redundant")}
    assert found["redundant_exception"]["severity"] == "warning"
    after = manifest.read_text()
    assert after == before["gateseal.toml"].decode().replace(
        commits["v1"], commits["v2"]
    ).replace('tag = "v1"', 'tag = "v2"')
    assert installed.cli(consumer, "check", env=env)["code"] == "consistent"

    back = upgrade(installed, consumer, env, "--policy-tag", "v1")
    assert targets(back)["pre-commit-hooks"] == ("6.0.0", "5.0.0", "rollback")
    assert exceptions(back) == {"betterleaks": ("1.9.0", "retained")}
    assert tracked(consumer) == before


def test_bare_component_follows_the_pin_forward_only(installed, team, tmp_path):
    consumer, env = consumer_at(installed, team, tmp_path, "v1", "secrets")
    upgrade(installed, consumer, env, "betterleaks=1.9.0", *REASON)
    before = tracked(consumer)
    kept = upgrade(installed, consumer, env, "betterleaks")
    assert kept["code"] == "unchanged" and kept["changed"] == []
    assert targets(kept)["betterleaks"] == ("1.9.0", "1.9.0", "preserved")
    assert tracked(consumer) == before
    renewed = upgrade(
        installed, consumer, env, "betterleaks", "--review-by", "2027-03-31"
    )
    assert exceptions(renewed) == {"betterleaks": ("1.9.0", "retained")}
    assert "review_by = 2027-03-31" in (consumer / "gateseal.toml").read_text()
    assert REASON[1] in (consumer / "gateseal.toml").read_text()

    upgrade(
        installed, consumer, env, "--policy-tag", "v2", "betterleaks=1.8.0", *REASON
    )
    forward = upgrade(installed, consumer, env, "betterleaks")
    assert targets(forward)["betterleaks"] == ("1.8.0", "1.9.0", "upgrade")
    assert exceptions(forward) == {"betterleaks": ("1.8.0", "removed")}
    assert "[exceptions.betterleaks]" not in (consumer / "gateseal.toml").read_text()


def test_noop_upgrade_writes_nothing(installed, team, tmp_path):
    consumer, env = consumer_at(installed, team, tmp_path, "v1", "secrets")
    before = tracked(consumer)
    for arguments in ([], ["--policy-tag", "v1"], ["pre-commit-hooks", "betterleaks"]):
        result = upgrade(installed, consumer, env, *arguments)
        assert (result["code"], result["changed"]) == ("unchanged", [])
        assert {state for _, _, state in targets(result).values()} == {"unchanged"}
    assert tracked(consumer) == before


def test_incompatible_exceptions_are_refused_before_any_write(
    installed, team, tmp_path
):
    consumer, env = consumer_at(installed, team, tmp_path, "v2", "secrets")
    upgrade(installed, consumer, env, "betterleaks=1.8.0", *REASON)
    before = tracked(consumer)
    refused = upgrade(installed, consumer, env, "--policy-tag", "v3", code=2)
    assert refused["code"] == "incompatible_exception"
    assert "exceptions' betterleaks 1.8.0." in refused["diagnostics"][0]["message"]
    assert "COMPONENT=VERSION" in refused["next_steps"][0]
    assert tracked(consumer) == before
    resolved = upgrade(installed, consumer, env, "--policy-tag", "v3", "betterleaks")
    assert targets(resolved)["betterleaks"] == ("1.8.0", "1.9.0", "upgrade")

    upgrade(installed, consumer, env, "pre-commit-hooks=5.0.0", *REASON)
    before = tracked(consumer)
    required = upgrade(installed, consumer, env, "--policy-tag", "v2", code=2)
    assert required["code"] == "incompatible_exception"
    assert (
        "betterleaks 1.9.0 needs pre-commit-hooks 6.0.0 or newer"
        in required["diagnostics"][0]["message"]
    )
    assert tracked(consumer) == before
    both = upgrade(installed, consumer, env, "--policy-tag", "v2", "pre-commit-hooks")
    assert exceptions(both) == {"pre-commit-hooks": ("5.0.0", "removed")}


def test_edited_output_is_refused_before_any_write(installed, team, tmp_path):
    consumer, env = consumer_at(installed, team, tmp_path, "v1", "secrets")
    rules = consumer / ".gateseal/betterleaks.toml"
    rules.write_text(rules.read_text() + "# local tweak\n")
    before = tracked(consumer)
    refused = upgrade(installed, consumer, env, "--policy-tag", "v2", code=2)
    assert refused["code"] == "ownership_conflict"
    assert ".gateseal/betterleaks.toml" in refused["diagnostics"][0]["message"]
    assert tracked(consumer) == before


def test_manifest_ahead_of_its_lock_names_the_recovery(installed, team, tmp_path):
    consumer, env = consumer_at(installed, team, tmp_path, "v1")
    manifest = consumer / "gateseal.toml"
    manifest.write_text(manifest.read_text().replace('tag = "v1"', 'tag = "v2"'))
    before = tracked(consumer)
    refused = upgrade(installed, consumer, env, "pre-commit-hooks", code=2)
    assert refused["code"] == "lock_mismatch"
    assert "git restore" in refused["next_steps"][0]
    assert tracked(consumer) == before


def test_bundled_and_git_targets_round_trip(installed, team, tmp_path):
    consumer, env = consumer_at(installed, team, tmp_path, "v1")
    before = tracked(consumer)
    bundled = upgrade(installed, consumer, env, "--policy", "bundled")
    assert 'source = "bundled"' in (consumer / "gateseal.toml").read_text()
    assert targets(bundled)["pre-commit-hooks"] == ("5.0.0", "6.0.0", "upgrade")
    upgrade(
        installed,
        consumer,
        env,
        "--policy",
        URL,
        "--policy-path",
        "policy",
        "--policy-tag",
        "v1",
    )
    assert tracked(consumer) == before


@pytest.mark.parametrize(
    ("arguments", "category"),
    [
        (["pre-commit"], "runtime_target"),
        (["ruff"], "unsupported_component"),
        (["shellcheck"], "unsupported_component"),
        (["betterleaks=1.7.0"], "unknown_version"),
        (["betterleaks=1.9.0"], "invalid_exception"),
        (["pre-commit-hooks", *REASON], "invalid_invocation"),
        (["betterleaks", "betterleaks=1.9.0"], "invalid_invocation"),
        (["betterleaks=latest"], "invalid_field"),
        (["--review-by", "tomorrow"], "invalid_invocation"),
        (["--policy-commit", "0" * 40], "invalid_invocation"),
    ],
)
def test_invalid_targets_write_nothing(installed, team, tmp_path, arguments, category):
    consumer, env = consumer_at(installed, team, tmp_path, "v1", "secrets")
    before = tracked(consumer)
    result = upgrade(installed, consumer, env, *arguments, code=2)
    assert result["code"] == category
    assert tracked(consumer) == before


def test_activation_failure_keeps_the_tracked_upgrade(installed, team, tmp_path):
    consumer, env = consumer_at(installed, team, tmp_path, "v1")
    failing = uv_shim(installed, tmp_path, "exit 7")
    failing = {**env, **{k: failing[k] for k in ("PATH", "GATESEAL_INTERPRETER_STORE")}}
    result = upgrade(
        installed, consumer, failing, "--policy-tag", "v2", "--activate", code=4
    )
    assert result["code"] == "activation_failed"
    assert sorted(result["changed"]) == [
        ".pre-commit-config.yaml",
        "gateseal.lock",
        "gateseal.toml",
    ]
    assert "Activation failed with tool_failed" in result["diagnostics"][0]["message"]
    assert result["next_steps"][0] == "gateseal activate"
    assert installed.cli(consumer, "check", env=env)["code"] == "consistent"
    assert installed.cli(consumer, "status", env=env)["code"] == "needs_activation"
    assert installed.cli(consumer, "activate", env=env)["code"] == "prepared"
    success(["git", "add", *TRACKED], consumer)
    commit_hygiene(consumer)


def test_upgrade_without_a_route_to_its_private_compiler(installed, team, tmp_path):
    """The clone activates from a downloaded wheel because its compiler is private.

    The upgrade's activation downloads the compiler from the Lock, and its next
    step names the local wheel that finishes the job without moving the pin.
    """
    origin, _ = team
    env = environment(installed, origin)
    consumer = clone(tmp_path / "origin", tmp_path / "consumer ü")
    url = json.loads((PACKAGE / "tests/artifact-source.json").read_bytes())["url"]
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
        "--compiler-source",
        url,
        "--compiler-sha256",
        installed.checksum,
        env=env,
    )
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    copy = shutil.copyfile(installed.wheel, downloads / installed.wheel.name)
    installed.cli(consumer, "activate", "--compiler-wheel", copy, env=env)
    result = upgrade(
        installed,
        consumer,
        unreachable(env),
        "--policy-tag",
        "v2",
        "--activate",
        code=4,
    )
    assert result["code"] == "activation_failed"
    assert result["next_steps"][0] == (
        f"Give this machine network access to {url}, then run gateseal activate. "
        f"Or save {url} as {installed.wheel.name} and run gateseal activate "
        "--compiler-wheel with its absolute path."
    )
    upgraded = tracked(consumer)
    activated = installed.cli(consumer, "activate", "--compiler-wheel", copy, env=env)
    assert activated["code"] == "prepared"
    assert tracked(consumer) == upgraded
    assert installed.cli(consumer, "doctor", env=env)["code"] == "healthy"
