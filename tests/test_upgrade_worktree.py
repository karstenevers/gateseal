"""Run gateseal upgrade in process on real Git worktrees.

Each consumer is configured in process with conftest's PIN for gateseal's
release tag. A team policy's releases are published into a local Git
repository that the canonical URL reaches through url.<base>.insteadOf, so
policy moves fetch real commits offline.
"""

import hashlib
import json
import os
import shutil
from datetime import date

import pytest
from conftest import PIN, TRACKED, with_git_config
from gateseal import __version__, lock, upgrade
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_bundled_policy
from gateseal.validation import canonical
from gateseal.worktree import locate_worktree, operation_lock, preparation_directory
from team_policy import URL, git, publish

REASON = "Our fixtures need the older rules"
REVIEW_BY = date(2027, 1, 31)
BUNDLED = lock.PolicyRequest()
BUNDLED_MOVE = lock.PolicyRequest("bundled")
POLICY = load_bundled_policy()


def older_pins(data):
    toml = data / "policy.toml"
    text = toml.read_text().replace('pin = "6.0.0"', 'pin = "5.0.0"')
    toml.write_text(text.replace('pin = "1.9.0"', 'pin = "1.8.0"'))


def requiring_hooks_6(data):
    toml = data / "policy.toml"
    toml.write_text(
        toml.read_text().replace(
            'pin = "1.9.0"\n',
            'pin = "1.9.0"\nrequires."1.9.0" = { pre-commit-hooks = "6.0.0" }\n',
        )
    )


def without_old_versions(data):
    toml = data / "policy.toml"
    text = toml.read_text().replace('["6.0.0", "5.0.0"]', '["6.0.0"]')
    toml.write_text(text.replace('["1.9.0", "1.8.0"]', '["1.9.0"]'))
    document = json.loads((data / "policy.lock").read_bytes())
    del document["components"]["pre-commit-hooks"]["versions"]["5.0.0"]
    del document["components"]["betterleaks"]["versions"]["1.8.0"]
    (data / "policy.lock").write_bytes(canonical(document))


def without_secrets(data):
    toml = data / "policy.toml"
    toml.write_text(toml.read_text().split("\n[capabilities.secrets.")[0])
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


def configure(root, *capabilities, policy=BUNDLED, ci=None):
    """Configure root as gateseal configure does; return its worktree."""
    worktree = locate_worktree(root)
    with operation_lock(preparation_directory(worktree)):
        lock.configure(
            worktree.root,
            worktree.git,
            worktree.preparation,
            list(capabilities),
            policy,
            ci,
        )
    return worktree


def adopted(root, tag, *capabilities):
    return configure(
        root,
        "hygiene",
        *capabilities,
        policy=lock.PolicyRequest(URL, None, "policy", tag),
    )


def request(policy=None, components=None, flags=(None, None)):
    return upgrade.Request(policy, components or {}, *flags)


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
    result = upgrade.run(worktree, request())
    assert (result.code, result.changed) == ("unchanged", ())
    assert result.next_steps == ()
    lines = result.details.split("\n")
    assert lines[0] == f"gateseal {__version__} at {PIN.commit}: unchanged."
    assert (
        f"policy bundled {POLICY.release} ({POLICY.content_digest}): unchanged."
        in lines
    )
    assert lines[-1] == "pre-commit-hooks 6.0.0: unchanged."
    assert {state for _, _, state in moves(result).values()} == {"unchanged"}
    assert tracked(consumer) == before


def written_by_a_newer_release(document):
    """Make document a Lock that gateseal 9.0.0 wrote, in fields only it reads.

    The ledger stays: the format, the pin, the Manifest's SHA-256, the policy's
    digest and outputs.
    """
    document["gateseal"].update(version="9.0.0", commit="9" * 40)
    document["policy"] = {"kind": "future", "digest": document["policy"]["digest"]}
    document["components"] = None
    document["future"] = "a field this release does not know"


def test_a_gateseal_move_reads_only_the_ledger_of_another_releases_lock(consumer):
    worktree = configure(consumer, "hygiene", ci="github")
    path = consumer / "gateseal.lock"
    current = path.read_bytes()
    document = json.loads(current)
    written_by_a_newer_release(document)
    path.write_bytes(canonical(document))
    result = upgrade.run(worktree, request())
    assert (result.code, result.changed) == ("upgraded", ("gateseal.lock",))
    assert path.read_bytes() == current
    old, new = f"9.0.0 at {'9' * 40}", f"{__version__} at {PIN.commit}"
    assert moves(result) == {"gateseal": (old, new, "rollback")}
    setup, review = result.next_steps
    assert f"uvx gateseal@{__version__} setup" in setup
    assert "git diff" in review


def bundled_by_a_newer_release(root, release, digest):
    """Make root's Manifest and Lock those of gateseal 9.0.0 and its bundled policy."""
    manifest = root / "gateseal.toml"
    label = f'release = "{POLICY.release}"'.encode()
    moved = manifest.read_bytes().replace(label, f'release = "{release}"'.encode())
    manifest.write_bytes(moved)
    path = root / "gateseal.lock"
    document = json.loads(path.read_bytes())
    written_by_a_newer_release(document)
    document["policy"]["digest"] = digest
    document["manifest_sha256"] = hashlib.sha256(moved).hexdigest()
    path.write_bytes(canonical(document))


@pytest.mark.parametrize(
    ("release", "digest", "changed"),
    [
        ("future-1", POLICY.content_digest, {"gateseal.toml", "gateseal.lock"}),
        (POLICY.release, "sha256:" + "0" * 64, {"gateseal.lock"}),
    ],
    ids=["another label", "other content under the label"],
)
def test_a_gateseal_move_takes_this_bundled_policy_only_when_asked(
    consumer, release, digest, changed
):
    worktree = configure(consumer, "hygiene")
    current = tracked(consumer)
    bundled_by_a_newer_release(consumer, release, digest)
    error = refused(worktree, request())
    assert error.code == "bundled_policy_moves"
    assert all(value in str(error) for value in (release, digest))
    assert "--policy bundled" in error.next_step
    result = upgrade.run(worktree, request(BUNDLED_MOVE))
    assert set(result.changed) == changed
    assert tracked(consumer) == current


@pytest.mark.parametrize(
    "asked",
    [
        request(components={"pre-commit-hooks": None}),
        request(lock.PolicyRequest(tag="v2")),
        request(flags=(REASON, REVIEW_BY)),
    ],
    ids=["component", "policy tag", "exception flags"],
)
def test_a_gateseal_move_moves_alone(consumer, asked):
    worktree = configure(consumer, "hygiene")
    path = consumer / "gateseal.lock"
    document = json.loads(path.read_bytes())
    written_by_a_newer_release(document)
    path.write_bytes(canonical(document))
    error = refused(worktree, asked)
    assert error.code == "invalid_invocation"
    assert "--policy bundled" in str(error)


def test_a_policy_move_fetches_writes_and_stores_the_new_release(
    consumer, team_commits
):
    worktree = adopted(consumer, "v1", "secrets")
    # As in a fresh clone, neither the preparation nor the v1 release is stored.
    shutil.rmtree(worktree.preparation)
    result = upgrade.run(worktree, request(lock.PolicyRequest(tag="v2")))
    assert result.code == "upgraded"
    assert sorted(result.changed) == [
        ".pre-commit-config.yaml",
        "gateseal.lock",
        "gateseal.toml",
    ]
    (review,) = result.next_steps
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
    assert loaded.policy.release == POLICY.release


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
        upgrade.run(locate_worktree(consumer), request())
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
