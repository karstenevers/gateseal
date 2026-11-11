"""Run gateseal upgrade in process on real Git worktrees.

Each consumer is configured in process with the compiler's network locator,
which configure records without downloading. A team policy's releases are
published into a local Git repository that the canonical URL reaches through
url.<base>.insteadOf, so policy moves fetch real commits offline.
"""

import json
import os
import shutil
from datetime import date

import pytest
from conftest import TRACKED, with_git_config
from gateseal import __version__, lock, runtime, upgrade
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_bundled_policy
from gateseal.validation import canonical
from team_policy import URL, git, publish

WHEEL = f"gateseal-{__version__}-py3-none-any.whl"
SOURCE = (
    f"https://raw.githubusercontent.com/example/wheels/{'0' * 40}/artifacts/{WHEEL}"
)
CHECKSUM = "a" * 64
REASON = "Our fixtures need the older rules"
REVIEW_BY = date(2027, 1, 31)
BUNDLED = lock.PolicyRequest()


def older_pins(data):
    toml = data / "policy.toml"
    text = toml.read_text().replace('pin = "6.0.0"', 'pin = "5.0.0"')
    toml.write_text(text.replace('pin = "1.9.0"', 'pin = "1.8.0"'))


def requiring_hooks_6(data):
    toml = data / "policy.toml"
    toml.write_text(
        toml.read_text().replace(
            '[components.betterleaks.versions."1.9.0"]\n',
            '[components.betterleaks.versions."1.9.0"]\n'
            'requires = { pre-commit-hooks = "6.0.0" }\n',
        )
    )


def without_old_versions(data):
    toml = data / "policy.toml"
    text = toml.read_text()
    for version in (
        'pre-commit-hooks.versions."5.0.0"',
        'betterleaks.versions."1.8.0"',
    ):
        text = text.replace(f"[components.{version}]\n", "")
    toml.write_text(text)
    document = json.loads((data / "policy.lock").read_bytes())
    del document["components"]["pre-commit-hooks"]["versions"]["5.0.0"]
    del document["components"]["betterleaks"]["versions"]["1.8.0"]
    (data / "policy.lock").write_bytes(canonical(document))


def without_secrets(data):
    toml = data / "policy.toml"
    text = toml.read_text()
    for section in (
        '[capabilities.secrets]\ntools = ["betterleaks"]\n\n',
        '[tools.betterleaks]\nconfiguration = "rules/betterleaks.toml"\n\n',
        '\n[components.betterleaks]\npin = "1.9.0"\n\n'
        '[components.betterleaks.versions."1.9.0"]\n\n'
        '[components.betterleaks.versions."1.8.0"]\n',
    ):
        text = text.replace(section, "")
    toml.write_text(text)
    (data / "rules/betterleaks.toml").unlink()
    document = json.loads((data / "policy.lock").read_bytes())
    del document["components"]["betterleaks"]
    (data / "policy.lock").write_bytes(canonical(document))


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """Publish v1 with older pins, v2 as bundled, and v3 to v5 that refuse v2's.

    v3's Betterleaks 1.9.0 needs pre-commit-hooks 6.0.0, v4 no longer reviews
    pre-commit-hooks 5.0.0 or Betterleaks 1.8.0, and v5 offers no secrets.
    """
    origin = tmp_path_factory.mktemp("upgrade team ü") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    releases = {
        "v1": older_pins,
        "v2": None,
        "v3": requiring_hooks_6,
        "v4": without_old_versions,
        "v5": without_secrets,
    }
    return origin, {tag: publish(origin, tag, edit) for tag, edit in releases.items()}


@pytest.fixture
def team_commits(team, monkeypatch):
    """Map the team policy's URL to its local origin; return each tag's commit."""
    origin, commits = team
    settings = {f"url.{origin.as_uri()}.insteadOf": URL}
    for key, value in with_git_config(dict(os.environ), settings).items():
        if key.startswith("GIT_CONFIG_"):
            monkeypatch.setenv(key, value)
    return commits


def configure(root, *capabilities, policy=BUNDLED):
    """Configure root as gateseal configure does; return its worktree."""
    worktree = runtime.locate_worktree(root)
    with runtime.operation_lock(runtime.preparation_directory(worktree, create=True)):
        lock.configure(
            worktree.root,
            worktree.git,
            worktree.preparation,
            list(capabilities),
            policy,
            None,
            SOURCE,
            CHECKSUM,
            None,
        )
    return worktree


def adopted(root, tag, *capabilities):
    return configure(
        root,
        "hygiene",
        *capabilities,
        policy=lock.PolicyRequest(URL, None, "policy", tag),
    )


def request(policy=None, components=None, flags=(None, None), *, activate=False):
    return upgrade.Request(policy, None, components or {}, *flags, activate)


def tracked(root):
    return {name: (root / name).read_bytes() for name in TRACKED}


def moves(result):
    return {
        entry["target"]: (entry["from"], entry["to"], entry["state"])
        for entry in result.entries
        if entry["category"] == "upgrade"
    }


def refused(worktree, asked):
    before = tracked(worktree.root)
    with pytest.raises(GatesealError) as raised:
        upgrade.run(worktree, asked)
    assert tracked(worktree.root) == before
    return raised.value


def test_an_upgrade_without_targets_writes_nothing(consumer):
    worktree = configure(consumer, "hygiene")
    before = tracked(consumer)
    policy = load_bundled_policy()
    same = (None, SOURCE, CHECKSUM)
    for asked in (request(), upgrade.Request(None, same, {}, None, None, False)):
        result = upgrade.run(worktree, asked)
        assert (result.code, result.changed) == ("unchanged", ())
        assert result.next_steps == ()
        lines = result.details.split("\n")
        assert lines[0] == f"compiler {__version__}: unchanged."
        assert (
            f"policy bundled {policy.release} ({policy.content_digest}): unchanged."
            in lines
        )
        assert lines[-1] == "pre-commit-hooks 6.0.0: unchanged."
        assert {state for _, _, state in moves(result).values()} == {"unchanged"}
    assert tracked(consumer) == before


def test_a_policy_move_fetches_writes_and_stores_the_new_release(
    consumer, team_commits
):
    worktree = adopted(consumer, "v1", "secrets")
    # As in a fresh clone, neither the preparation nor the v1 release is stored.
    shutil.rmtree(worktree.preparation)
    result = upgrade.run(worktree, request(lock.PolicyRequest(tag="v2")))
    assert result.code == "upgraded"
    assert sorted(result.changed) == [
        ".gateseal/mise.toml",
        ".pre-commit-config.yaml",
        "gateseal.lock",
        "gateseal.toml",
    ]
    activate, review = result.next_steps
    assert activate == "gateseal activate"
    assert "git diff" in review
    moved = moves(result)
    assert moved["pre-commit-hooks"] == ("5.0.0", "6.0.0", "upgrade")
    assert moved["betterleaks"] == ("1.8.0", "1.9.0", "upgrade")
    policy = moved["policy"]
    assert policy[0].startswith(f"v1 at {team_commits['v1']} (sha256:")
    assert policy[1].startswith(f"v2 at {team_commits['v2']} (sha256:")
    assert policy[2] == "change"
    manifest = (consumer / "gateseal.toml").read_text()
    assert f'commit = "{team_commits["v2"]}"' in manifest and 'tag = "v2"' in manifest
    loaded = lock.load(consumer, worktree.preparation)
    assert loaded.policy.release == load_bundled_policy().release


def test_policy_moves_that_break_the_exceptions_write_nothing(consumer, team_commits):
    worktree = adopted(consumer, "v1", "secrets")
    kept = {"pre-commit-hooks": "5.0.0", "betterleaks": "1.8.0"}
    upgraded = upgrade.run(
        worktree, request(lock.PolicyRequest(tag="v2"), kept, (REASON, REVIEW_BY))
    )
    assert moves(upgraded)["betterleaks"] == ("1.8.0", "1.8.0", "unchanged")

    unreviewed = refused(worktree, request(lock.PolicyRequest(tag="v4")))
    assert unreviewed.code == "incompatible_exception"
    named = ("betterleaks 1.8.0", "pre-commit-hooks 5.0.0")
    assert all(value in str(unreviewed) for value in named)
    assert unreviewed.next_step == upgrade.RESOLVE

    required = refused(
        worktree, request(lock.PolicyRequest(tag="v3"), {"betterleaks": None})
    )
    assert required.code == "incompatible_exception"
    named = ("betterleaks 1.9.0", "pre-commit-hooks 6.0.0")
    assert all(value in str(required) for value in named)
    assert required.next_step == upgrade.RESOLVE

    dropped = refused(worktree, request(lock.PolicyRequest(tag="v5")))
    assert dropped.code == "unsupported_capability"
    assert "secrets" in str(dropped)
    assert dropped.next_step == ""


def test_only_a_manifest_ahead_of_its_lock_names_the_restore(consumer):
    with pytest.raises(GatesealError) as missing:
        upgrade.run(runtime.locate_worktree(consumer), request())
    assert missing.value.code == "missing_configuration"
    assert missing.value.next_step == ""

    worktree = configure(consumer, "hygiene")
    manifest = consumer / "gateseal.toml"
    manifest.write_text(
        manifest.read_text().replace(
            "[choices]", '[choices.hygiene]\nexclude = ["vendor"]'
        )
    )
    ahead = refused(worktree, request())
    assert ahead.code == "lock_mismatch"
    assert ahead.next_step == upgrade.RESTORE


def test_an_edited_output_is_refused_before_any_write(consumer):
    worktree = configure(consumer, "hygiene")
    config = consumer / ".pre-commit-config.yaml"
    config.write_text(config.read_text() + "# local tweak\n")
    error = refused(worktree, request(components={"pre-commit-hooks": None}))
    assert error.code == "ownership_conflict"
    assert ".pre-commit-config.yaml" in str(error)
    # Loading the Lock with its outputs refuses with the same code. Only the
    # write check says preserve, so upgrade must leave outputs unread at load.
    assert "preserve" in str(error)


def test_a_failed_activation_keeps_the_upgraded_files(consumer):
    worktree = configure(consumer, "hygiene")
    hook = worktree.hook
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 0\n")
    older = request(
        components={"pre-commit-hooks": "5.0.0"},
        flags=(REASON, REVIEW_BY),
        activate=True,
    )
    result = upgrade.run(worktree, older)
    assert (result.code, result.exit_code) == ("activation_failed", 2)
    assert all(value in result.message for value in ("unmanaged_hook", str(hook)))
    preserve, review = result.next_steps
    assert str(hook) in preserve
    assert "git diff" in review
    assert sorted(result.changed) == sorted(TRACKED)
    assert 'version = "5.0.0"' in (consumer / "gateseal.toml").read_text()

    shutil.rmtree(hook.parent)
    hook.parent.write_bytes(b"")
    again = upgrade.run(worktree, request(activate=True))
    assert (again.code, again.exit_code) == ("activation_failed", 4)
    assert "filesystem_error" in again.message
    assert again.next_steps == ("gateseal activate",)
