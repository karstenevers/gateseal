"""configure pins a new Lock to the commit of this gateseal's release tag.

A local stand-in for gateseal's repository is reached through
url.<base>.insteadOf, as a user's own Git configuration can arrange.
"""

import os

import pytest
from conftest import with_git_config
from gateseal import __version__, lock
from gateseal.compiler_release import REPOSITORY, GatesealPin
from gateseal.diagnostics import GatesealError
from installation import success, tag_release

RELEASE_PIN = lock.release_pin
"""The real release_pin: tests import before conftest's release_tag stands in."""

IDENTITY = ["-c", "user.name=Release", "-c", "user.email=r@example.invalid"]


def reach(monkeypatch, origin):
    """Point gateseal's repository at origin through Git's own configuration."""
    settings = {f"url.{origin.as_uri()}.insteadOf": REPOSITORY}
    for key, value in with_git_config(dict(os.environ), settings).items():
        monkeypatch.setenv(key, value)


def pinned(tmp_path):
    preparation = tmp_path / "preparation"
    preparation.mkdir()
    return RELEASE_PIN("git", preparation)


@pytest.mark.parametrize("annotated", [False, True], ids=["lightweight", "annotated"])
def test_a_new_lock_pins_the_commit_of_the_release_tag(
    tmp_path, monkeypatch, annotated
):
    origin = tmp_path / "gateseal"
    commit = tag_release(origin, __version__)
    if annotated:
        tag = f"v{__version__}"
        success(["git", *IDENTITY, "tag", "-f", "-a", "-m", tag, tag], origin)
    reach(monkeypatch, origin)
    assert pinned(tmp_path) == GatesealPin(REPOSITORY, __version__, commit)


def test_a_repository_without_the_release_tag_is_refused(tmp_path, monkeypatch):
    origin = tmp_path / "gateseal"
    tag_release(origin, "0.0.1")
    reach(monkeypatch, origin)
    with pytest.raises(GatesealError) as failure:
        pinned(tmp_path)
    assert failure.value.code == "upstream_tag_missing"
    assert all(v in str(failure.value) for v in (REPOSITORY, f"v{__version__}"))


def test_an_unreachable_repository_names_git_ls_remote(tmp_path, monkeypatch):
    reach(monkeypatch, tmp_path / "missing")
    with pytest.raises(GatesealError) as failure:
        pinned(tmp_path)
    assert (failure.value.code, failure.value.exit_code) == ("upstream_unavailable", 4)
    assert f"git ls-remote {REPOSITORY}" in failure.value.next_step
