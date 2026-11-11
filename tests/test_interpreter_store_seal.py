import json
import os
import stat
from datetime import UTC, datetime

import pytest
from conftest import overwrite
from gateseal import bootstrap, interpreter_store
from gateseal.diagnostics import GatesealError

FIXED_MTIME_NS = int(datetime(2000, 1, 1, tzinfo=UTC).timestamp()) * 10**9
BUILT_MTIME_NS = 1_700_000_000 * 10**9
NAME = "e" * 64
KEY = "cpython-3.14.0-linux-x86_64-gnu"
IDENTITY = {"key": KEY}


@pytest.fixture
def store(monkeypatch, tmp_path):
    store = tmp_path.resolve() / "data" / "store"
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", str(store))
    return store


def attempt(tmp_path, digit):
    path = tmp_path / "gateseal" / ("attempt-" + digit * 32)
    path.mkdir(parents=True)
    return path


class Build:
    """Install a small interpreter tree at the entry's path and count the runs."""

    def __init__(self, mtime=None):
        self.mtime = mtime
        self.runs = 0

    def __call__(self, entry, lock):
        self.runs += 1
        entry.mkdir(mode=0o700)
        (entry / "bin").mkdir()
        python = entry / "bin" / "python3.14"
        python.write_text("#!/bin/sh\n")
        python.chmod(0o755)
        (entry / "bin" / "python3").symlink_to(python.name)
        (entry / "lib.py").write_text("VALUE = 1\n")
        if self.mtime is not None:
            for path in (python, entry / "lib.py"):
                os.utime(path, ns=(self.mtime, self.mtime))


def held(build, attempt, identity=IDENTITY, reproducible=True):
    with interpreter_store.sealed(
        NAME, identity, attempt, build, reproducible=reproducible
    ) as result:
        return result


def written_seal(entry):
    """Return the seal of entry as any gateseal release writes it: schema-1 JSON."""
    return {
        "schema": 1,
        **IDENTITY,
        "metadata": bootstrap.tree_digest(entry, (entry,), content=False),
        "content": bootstrap.tree_digest(entry, (entry,), content=True),
    }


def modes(entry):
    found = {}
    for directory, directories, files in os.walk(entry):
        for name in (".", *directories, *files):
            path = os.path.join(directory, name)
            info = os.lstat(path)
            if not stat.S_ISLNK(info.st_mode):
                found[os.path.relpath(path, entry)] = stat.S_IMODE(info.st_mode)
    return found


def test_a_new_entry_is_built_once_and_every_attempt_links_to_it(store, tmp_path):
    build = Build()
    first, second = attempt(tmp_path, "1"), attempt(tmp_path, "2")
    entry, digests = held(build, first)
    assert entry == store / NAME
    assert stat.S_IMODE(store.stat().st_mode) == 0o700
    assert digests == bootstrap.TreeDigests(
        bootstrap.tree_digest(entry, (entry,), content=False),
        bootstrap.tree_digest(entry, (entry,), content=True),
    )
    assert held(build, second) == (entry, digests)
    assert build.runs == 1
    refs = store / (NAME + ".refs")
    assert stat.S_IMODE(refs.stat().st_mode) == 0o700
    assert {name: os.readlink(refs / name) for name in os.listdir(refs)} == {
        first.name: str(first),
        second.name: str(second),
    }
    assert interpreter_store.linked(second, [entry / "bin" / "python3"])


@pytest.mark.parametrize("reproducible", [True, False])
def test_an_entry_becomes_read_only_and_a_reproducible_one_gets_the_fixed_mtime(
    store, tmp_path, reproducible
):
    entry, _ = held(
        Build(BUILT_MTIME_NS), attempt(tmp_path, "1"), reproducible=reproducible
    )
    assert modes(entry) == {
        ".": 0o555,
        "bin": 0o555,
        "bin/python3.14": 0o555,
        "lib.py": 0o444,
    }
    mtime = FIXED_MTIME_NS if reproducible else BUILT_MTIME_NS
    files = (entry / "bin" / "python3.14", entry / "lib.py")
    assert {path.stat().st_mtime_ns for path in files} == {mtime}
    assert os.readlink(entry / "bin" / "python3") == "python3.14"


def test_a_reinstall_at_the_same_path_reproduces_the_sealed_digests(store, tmp_path):
    build = Build()
    _, digests = held(build, attempt(tmp_path, "1"))
    (store / (NAME + ".json")).unlink()
    assert held(build, attempt(tmp_path, "2"))[1] == digests
    assert build.runs == 2


def test_a_seal_that_another_release_wrote_is_reused(store, tmp_path):
    build = Build()
    entry, digests = held(build, attempt(tmp_path, "1"))
    (store / (NAME + ".json")).write_text(json.dumps(written_seal(entry)))
    assert held(build, attempt(tmp_path, "2")) == (entry, digests)
    assert build.runs == 1


SEALS = {
    "not JSON": lambda seal: "{",
    "not an object": lambda seal: json.dumps([seal]),
    "an unknown field": lambda seal: json.dumps({**seal, "variant": "debug"}),
    "a missing digest": lambda seal: json.dumps(
        {field: value for field, value in seal.items() if field != "content"}
    ),
    "schema 2": lambda seal: json.dumps({**seal, "schema": 2}),
    "schema 1.0": lambda seal: json.dumps({**seal, "schema": 1.0}),
    "schema true": lambda seal: json.dumps({**seal, "schema": True}),
    "another archive": lambda seal: json.dumps({**seal, "key": "cpython-3.13.0"}),
}


@pytest.mark.parametrize("edit", SEALS.values(), ids=SEALS)
def test_an_entry_whose_seal_is_not_its_own_is_built_again(store, tmp_path, edit):
    build = Build()
    entry, _ = held(build, attempt(tmp_path, "1"))
    path = store / (NAME + ".json")
    path.write_text(edit(written_seal(entry)))
    _, digests = held(build, attempt(tmp_path, "2"))
    assert build.runs == 2
    seal = written_seal(entry)
    assert json.loads(path.read_bytes()) == seal
    assert digests == bootstrap.TreeDigests(seal["metadata"], seal["content"])


DAMAGE = {
    "content": lambda entry: overwrite(entry / "lib.py", b"VALUE = 2\n"),
    "mtime": lambda entry: os.utime(entry / "lib.py", ns=(0, 0)),
}


@pytest.mark.parametrize("damage", DAMAGE.values(), ids=DAMAGE)
def test_an_entry_that_lost_either_sealed_digest_is_built_again(
    store, tmp_path, damage
):
    build = Build()
    entry, digests = held(build, attempt(tmp_path, "1"))
    damage(entry)
    assert held(build, attempt(tmp_path, "2")) == (entry, digests)
    assert build.runs == 2
    assert (entry / "lib.py").read_bytes() == b"VALUE = 1\n"


def test_an_entry_sealed_for_another_identity_is_built_again(store, tmp_path):
    build = Build()
    identity = {**IDENTITY, "build": "20261101"}
    held(build, attempt(tmp_path, "1"), identity)
    held(build, attempt(tmp_path, "2"), {**identity, "build": "20261201"})
    assert build.runs == 2


def test_a_failed_rebuild_leaves_no_seal_behind(store, tmp_path):
    build = Build()
    entry, digests = held(build, attempt(tmp_path, "1"))
    overwrite(entry / "lib.py", b"VALUE = 2\n")

    def failing(entry, lock):
        entry.mkdir()
        raise RuntimeError("uv was killed")

    with pytest.raises(RuntimeError):
        held(failing, attempt(tmp_path, "2"))
    assert not (store / (NAME + ".json")).exists()
    assert held(build, attempt(tmp_path, "3")) == (entry, digests)


def test_a_build_that_leaves_a_fifo_is_refused(store, tmp_path):
    def build(entry, lock):
        entry.mkdir()
        os.mkfifo(entry / "pipe")

    with pytest.raises(GatesealError) as refused:
        held(build, attempt(tmp_path, "1"))
    assert (refused.value.code, refused.value.exit_code) == ("invalid_preparation", 4)
    assert str(store / NAME / "pipe") in str(refused.value)
    assert not (store / (NAME + ".json")).exists()


def test_an_attempt_is_never_linked_through_a_linked_refs_folder(store, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    store.mkdir(parents=True)
    (store / (NAME + ".refs")).symlink_to(elsewhere)
    with pytest.raises(OSError):
        held(Build(), attempt(tmp_path, "1"))
    assert list(elsewhere.iterdir()) == []


def test_no_attempt_is_linked_while_the_store_location_is_refused(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", "relative/store")
    paths = [tmp_path / "store" / NAME / "bin" / "python3"]
    assert interpreter_store.linked(attempt(tmp_path, "1"), paths) is False
