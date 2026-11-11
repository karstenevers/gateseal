"""Plan a compiler target with a candidate compiler.

The candidate is this compiler, started by the suite's Python as a separate
process. The current Lock state is compiled in process from a copy of the
bundled policy under another release name, as an older compiler bundled it.
"""

import json
import shutil
import sys
from datetime import date
from pathlib import Path

import pytest
from conftest import named_flags
from gateseal import __version__, candidate, lock, upgrade
from gateseal.bootstrap import CONFIG, LOCK, MANIFEST
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError
from gateseal.lock import Locked, Snapshot, compile_lock, compile_outputs
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import bundled_root, load_bundled_policy, load_policy_tree
from gateseal.processes import find_git
from gateseal.validation import canonical
from test_upgrade_worktree import SOURCE, WHEEL

CURRENT = CompilerArtifact("https-wheel", SOURCE, WHEEL, "a" * 64, __version__)
TARGET = CompilerArtifact("https-wheel", SOURCE, WHEEL, "c" * 64, __version__)
FLAGS = (None, SOURCE, TARGET.sha256)
BUNDLED = lock.PolicyRequest("bundled")


@pytest.fixture(scope="module")
def variant(tmp_path_factory):
    """The bundled policy as another compiler release bundles it."""
    root = tmp_path_factory.mktemp("variant policy") / "policy"
    shutil.copytree(bundled_root(), root, ignore=shutil.ignore_patterns("*.md"))
    toml = root / "policy.toml"
    toml.write_text(toml.read_text().replace('release = "', 'release = "variant-'))
    return load_policy_tree(root)


def configured(policy):
    """Return the Lock state of a hygiene consumer of policy's bundled release."""
    source = BundledSource(policy.release)
    manifest = render(("hygiene",), source)
    selection = resolve(parse_manifest(manifest), policy)
    document = canonical(compile_lock(manifest, source, policy, CURRENT, selection))
    outputs = compile_outputs(selection, CURRENT, source)
    snapshot = Snapshot(manifest, document, outputs)
    return Locked(Path(), policy, CURRENT, snapshot, source, selection)


def planned(current, root, policy=None, components=None, flags=(None, None)):
    request = upgrade.Request(policy, FLAGS, components or {}, *flags, False)
    compiler = candidate.Candidate(sys.executable, TARGET)
    return upgrade._plan_candidate(current, request, compiler, find_git(root), root)


@pytest.mark.parametrize(
    ("policy", "components", "flags"),
    [
        (None, {"pre-commit-hooks": None}, (None, None)),
        (None, None, ("Pinned for the migration", None)),
        (None, None, (None, date(2027, 1, 31))),
        (lock.PolicyRequest(tag="v2"), None, (None, None)),
        (lock.PolicyRequest(source="bundled", tag="v2"), None, (None, None)),
    ],
)
def test_a_compiler_target_moves_alone_or_with_the_bundled_policy(
    tmp_path, policy, components, flags
):
    current = configured(load_bundled_policy())
    with pytest.raises(GatesealError) as raised:
        planned(current, tmp_path, policy, components, flags)
    assert raised.value.code == "invalid_invocation"
    assert named_flags(str(raised.value)) == {"--policy"}
    assert not list(tmp_path.iterdir())


def test_the_bundled_policy_moves_with_the_compiler_only_when_asked(variant, tmp_path):
    current = configured(variant)
    bundled = load_bundled_policy()
    with pytest.raises(GatesealError) as raised:
        planned(current, tmp_path)
    assert raised.value.code == "bundled_policy_moves"
    named = (
        __version__,
        bundled.release,
        bundled.content_digest,
        variant.release,
        variant.content_digest,
    )
    assert all(value in str(raised.value) for value in named)
    assert "--policy bundled" in raised.value.next_step

    plan = planned(current, tmp_path, BUNDLED)
    assert sorted(plan.files) == sorted([CONFIG, LOCK, MANIFEST])
    old, new = (f'release = "{p.release}"'.encode() for p in (variant, bundled))
    assert plan.files[MANIFEST] == current.snapshot.manifest.replace(old, new)
    document = json.loads(plan.files[LOCK])
    assert document["compiler"] == TARGET.document()
    assert document["policy"] == {
        "kind": "bundled",
        "release": bundled.release,
        "digest": bundled.content_digest,
        "compiler_sha256": TARGET.sha256,
    }
    assert {note.category for note in plan.notes} >= {"policy", "runtime", "tool"}
    assert "configured" not in {note.category for note in plan.notes}
    (policy,) = (note for note in plan.notes if note.category == "policy")
    named = (bundled.release, __version__, bundled.content_digest)
    assert all(value in policy.message for value in named)
    assert (plan.preserved, plan.store) == (frozenset(), None)
    assert not list(tmp_path.glob(".gateseal-*"))
