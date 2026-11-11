"""A Git policy release: adopted by its tag, stored per worktree, read offline.

The team URL reaches a real local repository through url.<base>.insteadOf, as
in test_team_policy. Pointing it at a missing repository instead shows that a
call needs no Git: any fetch would fail with policy_fetch_failed.
"""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from gateseal import lock, sources
from gateseal.diagnostics import Note
from gateseal.manifest import GitSource
from gateseal.policy import load_policy_tree
from team_policy import URL, git, publish
from test_lock_configure import (
    CHECKSUM,
    CONFIG,
    LOCK,
    MANIFEST,
    OTHER_RELEASE,
    POLICY,
    SOURCE,
    configure,
    configured,
    edit_lock,
    other_compiler,
    outcome,
    preparation,
    refused,
    worktree,
)

DIGEST = POLICY.content_digest
ANOTHER_RELEASE = ("lock_mismatch", "gateseal configure")


@dataclass(frozen=True)
class Adoption:
    root: Path
    changed: tuple[str, ...]
    notes: tuple[Note, ...]
    source: GitSource


def reach(monkeypatch, origin):
    """Make Git fetch the team URL from origin."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", URL)


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    origin = tmp_path_factory.mktemp("lock team policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    return origin, publish(origin, "v1"), publish(origin, "v2", annotated=False)


@pytest.fixture(scope="module")
def adoption(team, tmp_path_factory):
    origin, first, _ = team
    root = worktree(tmp_path_factory.mktemp("lock git adoption"))
    request = lock.PolicyRequest(URL, None, "policy", "v1")
    with pytest.MonkeyPatch.context() as monkeypatch:
        reach(monkeypatch, origin)
        changed, notes = configure(
            root, ["hygiene"], request, source=SOURCE, checksum=CHECKSUM
        )
    return Adoption(root, changed, notes, GitSource(URL, "policy", first, "v1"))


@pytest.fixture
def offline(monkeypatch, tmp_path):
    reach(monkeypatch, tmp_path / "unreachable.git")


def copied(adoption, tmp_path, stored=True):
    """Return a copy of the adopted worktree, with its stored release or none."""
    root = tmp_path / "worktree"
    shutil.copytree(adoption.root, root)
    if stored:
        shutil.copytree(preparation(adoption.root), preparation(root))
    else:
        preparation(root).mkdir()
    return root


def other_policy(tmp_path):
    """Return a policy whose content differs from the bundled release's."""
    directory = tmp_path / "other policy"
    for asset in POLICY.assets:
        (directory / asset.path).parent.mkdir(parents=True, exist_ok=True)
        (directory / asset.path).write_bytes(asset.content)
    path = directory / "policy.toml"
    path.write_text(path.read_text().replace(POLICY.release, "another release"))
    return load_policy_tree(directory)


def store_entries(root):
    return sorted(path.name for path in preparation(root).glob("policies/*"))


def test_configure_adopts_a_git_release_by_its_tag(adoption):
    source = adoption.source
    assert adoption.changed == (MANIFEST, LOCK, CONFIG)
    tag, policy = adoption.notes[:2]
    assert (tag.severity, tag.category) == ("info", "policy_tag")
    assert all(value in tag.message for value in ("v1", source.commit))
    assert (policy.severity, policy.category) == ("info", "policy")
    named = (POLICY.release, URL, source.commit, "v1", DIGEST)
    assert all(value in policy.message for value in named)
    document = json.loads((adoption.root / LOCK).read_bytes())
    assert document["policy"] == {
        "kind": "git",
        "repository": URL,
        "path": "policy",
        "commit": source.commit,
        "tag": "v1",
        "release": POLICY.release,
        "digest": DIGEST,
    }
    assert f'commit = "{source.commit}"' in (adoption.root / MANIFEST).read_text()
    stored = sources.stored(preparation(adoption.root), source, DIGEST)
    assert stored is not None and stored.content_digest == DIGEST


def test_a_tag_alone_selects_the_commit_it_names(team, monkeypatch, tmp_path):
    origin, _, second = team
    reach(monkeypatch, origin)
    request = lock.PolicyRequest(URL, None, None, "v2")
    assert request.build("git", tmp_path) == GitSource(URL, ".", second, "v2")
    current = GitSource(URL, "policy", "1" * 40, "v1")
    target = lock.PolicyRequest(tag="v2").target(current, "git", tmp_path)
    assert target == GitSource(URL, "policy", second, "v2")


def test_a_rerun_reads_the_stored_release_without_git(adoption, tmp_path, offline):
    root = copied(adoption, tmp_path)
    changed, notes = configure(root)
    assert changed == ()
    assert notes[0] == adoption.notes[1]
    locked = lock.load(root, preparation(root))
    assert (locked.source, locked.policy.content_digest) == (adoption.source, DIGEST)


def test_adoption_without_a_lock_checks_the_tag_again(
    adoption, team, monkeypatch, tmp_path
):
    root = copied(adoption, tmp_path)
    (root / LOCK).unlink()
    reach(monkeypatch, team[0])
    changed, notes = configure(root, source=SOURCE, checksum=CHECKSUM)
    assert changed == (LOCK,)
    assert notes[:2] == adoption.notes[:2]


def test_load_needs_the_release_stored_in_this_worktree(adoption, tmp_path):
    root = copied(adoption, tmp_path, stored=False)
    error = refused(lock.load, root, preparation(root))
    assert outcome(error) == ("policy_unavailable", "gateseal activate")
    assert adoption.source.commit in str(error)
    assert error.exit_code == 3


def test_load_refuses_other_content_stored_for_the_locked_commit(adoption, tmp_path):
    root = copied(adoption, tmp_path, stored=False)
    other = other_policy(tmp_path)
    sources.store(preparation(root), adoption.source, other)
    error = refused(lock.load, root, preparation(root))
    assert outcome(error) == ("lock_mismatch", "gateseal configure")
    named = (adoption.source.commit, other.content_digest, LOCK, DIGEST)
    assert all(value in str(error) for value in named)


def another_commit(document):
    document["policy"]["commit"] = "2" * 40


def bundled_kind(document):
    document["policy"]["kind"] = "bundled"


def not_an_object(document):
    document["policy"] = "git"


@pytest.mark.parametrize("change", [another_commit, bundled_kind, not_an_object])
def test_load_refuses_a_lock_of_another_release(adoption, tmp_path, change):
    root = copied(adoption, tmp_path)
    edit_lock(root, change)
    assert outcome(refused(lock.load, root, preparation(root))) == ANOTHER_RELEASE


@pytest.mark.parametrize("digest", ["sha256:" + "A" * 64, 7])
@pytest.mark.parametrize(
    "call",
    [
        lambda root: lock.load(root, preparation(root)),
        lambda root: lock.ensure_policy(root, preparation(root), "git"),
    ],
    ids=["load", "ensure_policy"],
)
def test_a_malformed_locked_digest_is_named(adoption, tmp_path, offline, call, digest):
    root = copied(adoption, tmp_path)
    edit_lock(root, lambda document: document["policy"].update(digest=digest))
    error = refused(call, root)
    assert error.code == "invalid_field"
    assert "lock.policy.digest" in str(error)


def no_manifest(adoption, tmp_path):
    return worktree(tmp_path)


def bundled(adoption, tmp_path):
    return configured(tmp_path)


def no_lock(adoption, tmp_path):
    root = copied(adoption, tmp_path, stored=False)
    (root / LOCK).unlink()
    return root


def lock_not_json(adoption, tmp_path):
    root = copied(adoption, tmp_path, stored=False)
    (root / LOCK).write_bytes(b"{")
    return root


def lock_of_another_release(adoption, tmp_path):
    root = copied(adoption, tmp_path, stored=False)
    edit_lock(root, another_commit)
    return root


@pytest.mark.parametrize(
    "setup",
    [no_manifest, bundled, no_lock, lock_not_json, lock_of_another_release],
)
def test_ensure_policy_leaves_a_worktree_without_a_locked_git_release(
    adoption, tmp_path, offline, setup
):
    root = setup(adoption, tmp_path)
    assert lock.ensure_policy(root, preparation(root), "git") is None
    assert store_entries(root) == []


def test_ensure_policy_stores_the_locked_release_once(
    adoption, team, monkeypatch, tmp_path
):
    root = copied(adoption, tmp_path, stored=False)
    reach(monkeypatch, team[0])
    lock.ensure_policy(root, preparation(root), "git")
    [name] = store_entries(root)
    assert lock.load(root, preparation(root)).policy.content_digest == DIGEST
    entry = preparation(root) / "policies" / name
    identity = entry.stat().st_ino
    reach(monkeypatch, tmp_path / "unreachable.git")
    lock.ensure_policy(root, preparation(root), "git")
    assert entry.stat().st_ino == identity


def test_ensure_policy_names_another_compilers_lock_before_any_fetch(
    adoption, tmp_path, offline
):
    root = copied(adoption, tmp_path, stored=False)
    edit_lock(root, other_compiler)
    error = refused(lock.ensure_policy, root, preparation(root), "git")
    assert error.code == "selected_compiler_required"
    assert OTHER_RELEASE in str(error)


def test_ensure_policy_refuses_a_commit_without_the_locked_digest(
    adoption, team, monkeypatch, tmp_path
):
    root = copied(adoption, tmp_path, stored=False)
    locked = "sha256:" + "0" * 64
    edit_lock(root, lambda document: document["policy"].update(digest=locked))
    reach(monkeypatch, team[0])
    error = refused(lock.ensure_policy, root, preparation(root), "git")
    assert outcome(error) == ("lock_mismatch", "gateseal configure")
    named = (adoption.source.commit, DIGEST, LOCK, locked)
    assert all(value in str(error) for value in named)
    assert store_entries(root) == []
