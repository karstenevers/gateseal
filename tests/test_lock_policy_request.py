"""configure's and upgrade's policy flags, read without asking Git.

A request that names its commit needs no Git, so these calls get a Git that
cannot start: reaching it would fail with tool_unavailable.
"""

import pytest
from conftest import named_flags
from gateseal.lock import PolicyRequest
from gateseal.manifest import BundledSource, GitSource
from test_lock_configure import MANIFEST, POLICY, outcome, refused

URL = "https://policy.example/team/gateseal-policy.git"
OTHER = "https://policy.example/other/gateseal-policy.git"
FIRST, SECOND = "1" * 40, "2" * 40
CURRENT = GitSource(URL, "policy", FIRST, "v1")
BUNDLED = BundledSource(POLICY.release)
GIT_FLAGS = {"--policy-commit", "--policy-path", "--policy-tag", "--policy"}


@pytest.fixture
def nogit(tmp_path):
    return str(tmp_path / "missing" / "git")


@pytest.mark.parametrize("source", [None, "bundled"])
def test_no_repository_selects_the_bundled_release(tmp_path, nogit, source):
    assert PolicyRequest(source).build(nogit, tmp_path) == BUNDLED


@pytest.mark.parametrize(
    "request_",
    [
        PolicyRequest(commit=FIRST),
        PolicyRequest(path="policy"),
        PolicyRequest("bundled", tag="v1"),
    ],
    ids=["commit", "path", "tag"],
)
def test_git_flags_need_a_repository(tmp_path, nogit, request_):
    error = refused(request_.build, nogit, tmp_path)
    assert outcome(error) == ("invalid_invocation", "")
    assert named_flags(str(error)) == GIT_FLAGS


def test_a_git_policy_needs_its_tag(tmp_path, nogit):
    error = refused(PolicyRequest(URL, FIRST, "policy").build, nogit, tmp_path)
    assert error.code == "invalid_invocation"
    assert named_flags(str(error)) == {"--policy-tag"}


@pytest.mark.parametrize(
    "path,expected", [(None, "."), ("policy", "policy")], ids=["root", "directory"]
)
def test_a_named_commit_selects_itself(tmp_path, nogit, path, expected):
    request = PolicyRequest(URL, SECOND, path, "v1")
    assert request.build(nogit, tmp_path) == GitSource(URL, expected, SECOND, "v1")


def test_a_named_commit_is_still_validated(tmp_path, nogit):
    error = refused(PolicyRequest(URL, "abc", None, "v1").build, nogit, tmp_path)
    assert error.code == "invalid_policy_source"
    assert "policy.commit" in str(error)


@pytest.mark.parametrize("current", [CURRENT, BUNDLED], ids=["git", "bundled"])
def test_an_upgrade_to_bundled_selects_this_compilers_release(tmp_path, nogit, current):
    assert PolicyRequest("bundled").target(current, nogit, tmp_path) == BUNDLED
    request = PolicyRequest("bundled", commit=SECOND)
    error = refused(request.target, current, nogit, tmp_path)
    assert outcome(error) == ("invalid_invocation", "")
    assert named_flags(str(error)) == GIT_FLAGS


@pytest.mark.parametrize(
    "request_,expected",
    [
        (
            PolicyRequest(commit=SECOND, tag="v2"),
            GitSource(URL, "policy", SECOND, "v2"),
        ),
        (
            PolicyRequest(URL, SECOND, tag="v2"),
            GitSource(URL, "policy", SECOND, "v2"),
        ),
        (
            PolicyRequest(commit=SECOND, path="sub", tag="v2"),
            GitSource(URL, "sub", SECOND, "v2"),
        ),
        (PolicyRequest(OTHER, SECOND, tag="v2"), GitSource(OTHER, ".", SECOND, "v2")),
        (
            PolicyRequest(OTHER, SECOND, "sub", "v2"),
            GitSource(OTHER, "sub", SECOND, "v2"),
        ),
    ],
    ids=["same", "same named", "new path", "other repository", "other with path"],
)
def test_an_upgrade_keeps_the_repository_and_directory_it_does_not_name(
    tmp_path, nogit, request_, expected
):
    assert request_.target(CURRENT, nogit, tmp_path) == expected


def test_an_upgrade_from_bundled_names_its_repository(tmp_path, nogit):
    request = PolicyRequest(URL, SECOND, tag="v2")
    assert request.target(BUNDLED, nogit, tmp_path) == GitSource(URL, ".", SECOND, "v2")
    request = PolicyRequest(commit=SECOND, tag="v2")
    error = refused(request.target, BUNDLED, nogit, tmp_path)
    assert outcome(error) == ("invalid_invocation", "")
    assert named_flags(str(error)) == {"--policy", "--policy-tag"}


@pytest.mark.parametrize(
    "source,request_",
    [
        (BUNDLED, PolicyRequest()),
        (BUNDLED, PolicyRequest("bundled")),
        (CURRENT, PolicyRequest()),
        (CURRENT, PolicyRequest(URL, FIRST, "policy", "v1")),
        (CURRENT, PolicyRequest(tag="v1")),
    ],
)
def test_flags_that_match_the_manifest_pass(source, request_):
    request_.check(source)


@pytest.mark.parametrize(
    "source,request_,flags",
    [
        (BUNDLED, PolicyRequest(URL), {"--policy"}),
        (BUNDLED, PolicyRequest(commit=FIRST), {"--policy-commit"}),
        (CURRENT, PolicyRequest("bundled"), {"--policy"}),
        (CURRENT, PolicyRequest(path="."), {"--policy-path"}),
        (CURRENT, PolicyRequest(OTHER, SECOND, "sub", "v2"), GIT_FLAGS),
    ],
)
def test_flags_that_differ_from_the_manifest_are_named(source, request_, flags):
    error = refused(request_.check, source)
    assert error.code == "configure_assertion_failed"
    assert MANIFEST in str(error)
    assert named_flags(str(error)) == flags
    assert "[policy]" in error.next_step
    assert "gateseal configure" in error.next_step
