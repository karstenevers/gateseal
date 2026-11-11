import contextlib
import ctypes
import fcntl
import os
import stat
import sys

import pytest
from gateseal import interpreter_store

KEPT, DROPPED, OTHER = "a" * 64, "b" * 64, "c" * 64
CAPABILITY_VERSION_3 = 0x20080522
MODE_OVERRIDES = 1 << 1 | 1 << 2
"""CAP_DAC_OVERRIDE and CAP_DAC_READ_SEARCH, with which root ignores file modes."""


class _CapabilityHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapabilitySet(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


@contextlib.contextmanager
def owner_permissions():
    """Make file modes bind the suite as they bind the ordinary user who owns a store.

    Root ignores modes, so under root this thread gives up the capabilities
    that let it until the block ends.
    """
    if os.geteuid() != 0:
        yield
        return
    if sys.platform != "linux":
        pytest.skip("Only Linux lets root stop overriding file modes.")
    libc = ctypes.CDLL(None, use_errno=True)
    header, sets = _CapabilityHeader(CAPABILITY_VERSION_3, 0), (_CapabilitySet * 2)()
    if libc.capget(ctypes.byref(header), sets):
        raise OSError(ctypes.get_errno(), "capget failed")
    effective = sets[0].effective
    sets[0].effective &= ~MODE_OVERRIDES
    if libc.capset(ctypes.byref(header), sets):
        raise OSError(ctypes.get_errno(), "capset failed")
    try:
        yield
    finally:
        sets[0].effective = effective
        libc.capset(ctypes.byref(header), sets)


@pytest.fixture
def store(monkeypatch, tmp_path):
    store = tmp_path.resolve() / "store"
    store.mkdir()
    (store / "store.lock").touch()
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", str(store))
    return store


def attempt(digit):
    return "attempt-" + digit * 32


def plant(store, name, links=None):
    """Plant a sealed entry like a virtual environment, and its refs folder."""
    entry = store / name
    (entry / "lib" / "python3.14").mkdir(parents=True)
    (entry / "lib" / "python3.14" / "os.py").write_text("")
    (entry / "lib64").symlink_to("lib")
    (entry / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (store / (name + ".json")).write_text("{}")
    refs = store / (name + ".refs")
    if links is not None:
        refs.mkdir()
        for link, target in links.items():
            (refs / link).symlink_to(target)
    return entry


def closed_folder(path, mode=0o555, links=None):
    path.mkdir()
    (path / "kept").write_text("")
    for link, target in (links or {}).items():
        (path / link).symlink_to(target)
    path.chmod(mode)
    return path


def test_an_entry_keeps_only_the_links_of_attempts_that_exist(store, tmp_path):
    live = tmp_path / "live attempt"
    live.mkdir()
    plant(store, KEPT, {attempt("1"): live, attempt("2"): tmp_path / "deleted"})
    refs = store / (KEPT + ".refs")
    (refs / "notes.txt").write_text("")
    (refs / "partial").mkdir()
    (refs / "partial" / "x").write_text("")
    (refs / attempt("3")).write_text("")
    interpreter_store.collect()
    assert set(os.listdir(store)) == {
        "store.lock",
        KEPT,
        KEPT + ".json",
        KEPT + ".refs",
    }
    assert os.listdir(refs) == [attempt("1")]
    assert os.readlink(refs / attempt("1")) == str(live)


def test_an_entry_that_no_existing_attempt_links_to_is_removed(store, tmp_path):
    live, file = tmp_path / "live attempt", tmp_path / "file"
    live.mkdir()
    file.write_text("")
    entry = plant(
        store,
        DROPPED,
        {
            attempt("1"): tmp_path / "deleted",
            attempt("2"): file,
            attempt("3"): file / "attempt",
            "attempt-copy": live,
        },
    )
    (entry / "lib").chmod(0o555)
    entry.chmod(0o555)
    interpreter_store.collect()
    assert set(os.listdir(store)) == {"store.lock"}
    assert live.is_dir()
    assert file.is_file()


def test_an_entry_whose_attempt_cannot_be_checked_is_kept(store, tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop)
    plant(store, OTHER, {attempt("1"): loop})
    interpreter_store.collect()
    assert set(os.listdir(store)) == {
        "store.lock",
        OTHER,
        OTHER + ".json",
        OTHER + ".refs",
    }
    assert os.listdir(store / (OTHER + ".refs")) == [attempt("1")]


def test_seals_refs_and_interrupted_writes_without_an_entry_are_removed(
    store, tmp_path
):
    (store / (KEPT + ".json")).write_text("{}")
    (store / (DROPPED + ".refs")).mkdir()
    (store / (DROPPED + ".refs") / attempt("1")).symlink_to(tmp_path / "deleted")
    plant(store, OTHER)
    (store / (OTHER + ".refs")).write_text("")
    (store / ".gateseal-0123abcd").write_text("")
    (store / "notes.txt").write_text("")
    # Upper-case hex names no entry. It differs from every key here in more
    # than case, so a case-insensitive file system such as APFS keeps it apart.
    (store / ("F" * 64)).mkdir()
    interpreter_store.collect()
    assert set(os.listdir(store)) == {"store.lock", "notes.txt", "F" * 64}


def test_collection_never_follows_a_link_out_of_the_store(store, tmp_path):
    live = tmp_path / "live attempt"
    live.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    folders = [
        closed_folder(outside / "entry"),
        closed_folder(outside / "refs", links={attempt("1"): live}),
        closed_folder(outside / "junk"),
        closed_folder(outside / "site"),
    ]
    (store / DROPPED).symlink_to(folders[0])
    plant(store, OTHER)
    (store / (OTHER + ".refs")).symlink_to(folders[1])
    plant(store, KEPT, {attempt("1"): live, "junk": folders[2]})
    plant(store, "d" * 64, {})
    (store / ("d" * 64) / "site").symlink_to(folders[3])
    interpreter_store.collect()
    assert set(os.listdir(store)) == {
        "store.lock",
        KEPT,
        KEPT + ".json",
        KEPT + ".refs",
    }
    assert os.listdir(store / (KEPT + ".refs")) == [attempt("1")]
    for folder in folders:
        assert stat.S_IMODE(folder.stat().st_mode) == 0o555
        assert "kept" in os.listdir(folder)
    assert os.readlink(folders[1] / attempt("1")) == str(live)


def test_an_entry_whose_folders_are_read_only_or_closed_is_removed(store):
    entry = plant(store, DROPPED, {})
    closed_folder(entry / "lib" / "python3.14" / "closed", 0)
    (entry / "lib" / "python3.14").chmod(0o555)
    (entry / "lib").chmod(0o555)
    entry.chmod(0)
    with owner_permissions():
        interpreter_store.collect()
    assert set(os.listdir(store)) == {"store.lock"}


def test_a_store_that_another_activation_holds_is_collected_later(store):
    plant(store, DROPPED, {})
    descriptor = os.open(store / "store.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        interpreter_store.collect()
        assert DROPPED in os.listdir(store)
    finally:
        os.close(descriptor)
    interpreter_store.collect()
    assert set(os.listdir(store)) == {"store.lock"}
