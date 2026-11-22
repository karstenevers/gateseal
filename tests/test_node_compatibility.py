"""A gateseal older than a Node policy's minimum_compiler stops before any write.

A policy with node and an npm-package tool needs a gateseal that reads both.
Its minimum_compiler names that release, so an older gateseal stops with
incompatible_compiler before it reads node or the kind.
"""

from conftest import BUNDLED_MINIMUM
from gateseal import __version__, authoring, lock
from gateseal.policy import review_policy
from node_policy import POLICY as DOCS
from node_policy import docs, releases  # noqa: F401
from team_policy import URL, git, publish
from test_lock_configure import OTHER_RELEASE, configure, refused, tracked, worktree
from test_lock_git_policy import reach

MINIMUM = 'minimum_compiler = "0.2.0"\n'
"""The minimum of the policy of the docs."""
NEWER = f'minimum_compiler = "{OTHER_RELEASE}"\n'


def newer(data):
    """Require a gateseal newer than this one in the bundled policy copy in data."""
    policy = data / "policy.toml"
    text = policy.read_text()
    assert f"{BUNDLED_MINIMUM}\n" in text and 'node = "' in text
    policy.write_text(text.replace(f"{BUNDLED_MINIMUM}\n", NEWER))


def test_policy_lock_and_configure_stop_before_any_write(
    tmp_path,
    releases,  # noqa: F811
    monkeypatch,
):
    assert MINIMUM in DOCS
    root = docs(tmp_path, DOCS.replace(MINIMUM, NEWER))
    before = tracked(root)
    failure = refused(authoring.lock, root)
    assert failure.code == "incompatible_compiler"
    assert failure.next_step == f"Use gateseal {OTHER_RELEASE} or newer."
    assert tracked(root) == before
    assert releases.downloaded == []

    origin = tmp_path / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", newer)
    reach(monkeypatch, origin)
    consumer = worktree(tmp_path)
    request = lock.PolicyRequest(URL, None, "policy", "v1")
    failure = refused(configure, consumer, ["typescript"], request)
    assert failure.code == "incompatible_compiler"
    assert tracked(consumer) == {}


def test_an_older_compiler_meets_minimum_compiler_before_node(tmp_path):
    policy = DOCS.replace(MINIMUM, f'minimum_compiler = "{__version__}"\n')
    root = docs(tmp_path, policy)
    failure = refused(
        review_policy, lambda name: (root / name).read_bytes(), compiler="0.1.0"
    )
    assert failure.code == "incompatible_compiler"
    assert str(failure) == (
        f"The policy requires gateseal {__version__} or newer; this is 0.1.0."
    )


def test_the_bundled_policy_needs_the_release_that_ships_it():
    """An older gateseal stops on the bundled policy with incompatible_compiler,
    never on a field it cannot read, such as node."""
    assert BUNDLED_MINIMUM == f'minimum_compiler = "{__version__}"'
