"""Refuse a Policy Lock that disagrees with policy.toml, before anything runs.

Every load of a policy checks its lock, so configure, activation, adoption and
gateseal policy check refuse it the same way.
"""

import json

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_policy_tree
from test_contracts import relock, tree

STEP = "Run gateseal policy lock in the policy directory."


def refused(root):
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(root)
    assert failure.value.next_step == STEP
    return failure.value


def test_a_policy_without_its_lock_is_refused(tmp_path):
    tree(tmp_path)
    (tmp_path / "policy.lock").unlink()
    error = refused(tmp_path)
    assert (error.code, str(error)) == (
        "policy_lock_missing",
        "The policy has no policy.lock.",
    )


def test_a_newly_reviewed_version_makes_the_lock_stale(tmp_path):
    tree(tmp_path)
    path = tmp_path / "policy.toml"
    path.write_text(
        path.read_text() + '\n[components.ruff.versions."0.17.0"]\n'
        '\n[components.betterleaks.versions."1.7.0"]\n'
    )
    error = refused(tmp_path)
    assert (error.code, str(error)) == (
        "policy_lock_stale",
        "policy.lock has no facts for betterleaks 1.7.0, ruff 0.17.0, which "
        "policy.toml reviews.",
    )


def test_a_lock_that_lists_an_unreviewed_version_is_refused(tmp_path):
    tree(tmp_path)
    path = tmp_path / "policy.toml"
    path.write_text(
        path.read_text().replace('[components.pre-commit-hooks.versions."5.0.0"]\n', "")
    )
    error = refused(tmp_path)
    assert (error.code, str(error)) == (
        "policy_lock_unreviewed",
        "policy.lock lists pre-commit-hooks 5.0.0, which policy.toml does not review.",
    )


def test_a_lock_for_a_component_the_policy_lacks_is_unreviewed(tmp_path):
    tree(tmp_path)
    relock(
        tmp_path,
        lambda components: components.update(
            black={"versions": {"26.1.0": {"revision": "a" * 40}}}
        ),
    )
    assert refused(tmp_path).code == "policy_lock_unreviewed"


@pytest.mark.parametrize(
    "edit",
    [
        lambda data: json.dumps(json.loads(data)).encode(),
        lambda data: data.replace(b'"gateseal policy lock"', b'"hand"'),
        lambda data: data.replace(b"3e8a8703264a", b"3E8A8703264A"),
        lambda data: data.replace(b'"revision": "3e8a87', b'"commit": "3e8a87'),
        lambda data: data[:-3],
        lambda data: data.replace(b'"components": {', b'"schema": 1, "components": {'),
    ],
    ids=[
        "reformatted",
        "generator",
        "uppercase revision",
        "field name",
        "truncated",
        "unknown field",
    ],
)
def test_a_hand_edited_lock_is_refused(tmp_path, edit):
    tree(tmp_path)
    path = tmp_path / "policy.lock"
    path.write_bytes(edit(path.read_bytes()))
    assert refused(tmp_path).code == "policy_lock_edited"
