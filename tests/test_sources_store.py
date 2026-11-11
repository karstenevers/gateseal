"""Keep each acquired policy release in the preparation directory, by digest."""

import stat

import pytest
from gateseal import sources
from gateseal.diagnostics import GatesealError
from gateseal.manifest import GitSource
from team_policy import POLICY, URL

SOURCE = GitSource(URL, "policy", "a" * 40, "v1")
OTHER = GitSource(URL, "policy", "b" * 40, "v2")


@pytest.fixture
def preparation(tmp_path):
    directory = tmp_path / "gateseal"
    directory.mkdir()
    return directory


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_a_stored_release_reads_back_only_for_its_source_and_digest(preparation):
    assert sources.stored_any(preparation, SOURCE) is None
    sources.store(preparation, SOURCE, POLICY)
    assert sources.stored(preparation, SOURCE, POLICY.content_digest) == POLICY
    assert sources.stored_any(preparation, SOURCE) == POLICY
    assert sources.stored(preparation, SOURCE, "sha256:" + "0" * 64) is None
    assert sources.stored(preparation, OTHER, POLICY.content_digest) is None
    assert sources.stored_any(preparation, OTHER) is None
    store = preparation / "policies"
    assert mode(store) == 0o700
    [release] = store.iterdir()
    assert mode(release) == 0o644


def test_storing_clears_what_an_interrupted_run_left(preparation):
    store = preparation / "policies"
    store.mkdir()
    (store / ".gateseal-0123").write_text("partial\n")
    sources.store(preparation, OTHER, POLICY)
    kept = {path.name for path in store.iterdir()}
    sources.store(preparation, SOURCE, POLICY)
    assert {path.name for path in store.iterdir()} > kept
    assert ".gateseal-0123" not in kept
    assert sources.stored_any(preparation, OTHER) == POLICY


def damaged(preparation, change):
    sources.store(preparation, SOURCE, POLICY)
    [release] = (preparation / "policies").iterdir()
    change(release)
    with pytest.raises(GatesealError) as failure:
        sources.stored(preparation, SOURCE, POLICY.content_digest)
    return release, failure.value


def test_a_changed_release_is_damaged_preparation(preparation):
    release, error = damaged(preparation, lambda path: path.write_bytes(b"edited"))
    assert (error.code, error.exit_code) == ("policy_content_mismatch", 3)
    assert f"policies/{release.name}" in str(error)
    assert error.next_step == "gateseal activate"


def test_an_executable_release_is_refused(preparation):
    _, error = damaged(preparation, lambda path: path.chmod(0o755))
    assert error.code == "file_mode_mismatch"
