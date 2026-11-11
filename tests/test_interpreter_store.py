import fcntl
import json
import os
import platform
import shlex
import shutil
import stat
import subprocess
import warnings
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import (
    TRACKED,
    commit_hygiene,
    overwrite,
    prepared_state,
    published,
    remove_file,
    uv_shim,
)
from gateseal import bootstrap, interpreter_store
from gateseal.compiler_release import TARGETS, load_runtime
from gateseal.diagnostics import GatesealError
from installation import clone, success

FIXED_MTIME_NS = int(datetime(2000, 1, 1, tzinfo=UTC).timestamp()) * 10**9
ARCHIVE = interpreter_store.Archive(
    load_runtime().interpreter(TARGETS[(platform.system(), platform.machine())])
)
SHA = ARCHIVE.sha256


def hook_entries(store):
    """Return the names of store's hook environment entries, read from their seals."""
    return {
        seal.stem
        for seal in store.glob("*.json")
        if "recipe" in json.loads(seal.read_bytes())
    }


def sealed(store):
    """Return what a clean store holds: the interpreter and one hygiene hook entry."""
    (hooks,) = hook_entries(store)
    return {"store.lock"} | {
        name + suffix for name in (SHA, hooks) for suffix in ("", ".json", ".refs")
    }


def store_environment(installed, store):
    return dict(installed.environment, GATESEAL_INTERPRETER_STORE=str(store))


def configure(installed, env, directory):
    root = clone(directory / "origin", directory / "consumer ü")
    installed.cli(
        root,
        "configure",
        "--capability",
        "hygiene",
        "--compiler-wheel",
        str(installed.wheel),
        "--compiler-sha256",
        installed.checksum,
        env=env,
    )
    return root


def adopt(installed, env, directory):
    root = configure(installed, env, directory)
    assert installed.cli(root, "activate", env=env)["code"] == "prepared"
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "-m", "Adopt hygiene"], root)
    return root


def links(store):
    refs = store / (SHA + ".refs")
    return {name: Path(os.readlink(refs / name)) for name in os.listdir(refs)}


def logging_shim(installed, tmp_path, log, seconds=0):
    """Return an environment whose uv logs each `uv python install` it runs."""
    return uv_shim(
        installed,
        tmp_path,
        f"echo install >> {shlex.quote(str(log))}; sleep {seconds}; "
        'exec "{uv}" python install "$@"',
    )


def installs(log):
    return len(log.read_text().splitlines()) if log.exists() else 0


def entry_file(store, name):
    return next((store / SHA).glob(f"cpython-3.*.*-*/lib/python3.*/{name}"))


def plant_garbage(store):
    """Plant an unreferenced read-only entry, its seal, a dead link and a leftover."""
    fake = "f" * 64
    (store / fake).mkdir()
    (store / fake / "python").write_text("fake\n")
    (store / fake / "python").chmod(0o444)
    (store / fake).chmod(0o555)
    (store / (fake + ".json")).write_text("{}")
    refs = store / (fake + ".refs")
    refs.mkdir()
    (refs / ("attempt-" + "0" * 32)).symlink_to(store / "missing attempt")
    dead = store / (SHA + ".refs") / ("attempt-" + "1" * 32)
    dead.symlink_to(store / "deleted attempt")
    (store / ".gateseal-x").write_text("leftover\n")
    return {fake, fake + ".json", fake + ".refs", ".gateseal-x"}


def test_two_repositories_share_one_sealed_interpreter(installed, tmp_path):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    first = adopt(installed, env, tmp_path / "first")
    second = adopt(installed, env, tmp_path / "second")
    states = [prepared_state(root) for root in (first, second)]
    interpreter = Path(states[0]["paths"]["interpreter"])
    assert interpreter.is_relative_to(store / SHA)
    assert set(os.listdir(store)) == sealed(store)
    assert links(store) == {published(r).name: published(r) for r in (first, second)}
    for root, state in zip((first, second), states, strict=True):
        assert state["paths"]["interpreter"] == str(interpreter)
        assert not (published(root) / "python").exists()
        pythons = [
            published(root) / "hook" / "python",
            Path(state["paths"]["compiler_python"]),
            Path(state["paths"]["pre_commit_python"]),
            *(Path(hook["python"]) for hook in state["hook_environments"].values()),
        ]
        assert {python.resolve() for python in pythons} == {interpreter}
    for directory, _, files in os.walk(store / SHA):
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o555
        for name in files:
            info = os.lstat(os.path.join(directory, name))
            if stat.S_ISREG(info.st_mode):
                assert stat.S_IMODE(info.st_mode) in (0o444, 0o555)
                assert info.st_mtime_ns == FIXED_MTIME_NS
    for root in (first, second):
        commit_hygiene(root)


def test_concurrent_activations_install_the_interpreter_once(installed, tmp_path):
    log = tmp_path / "installs.log"
    env = logging_shim(installed, tmp_path, log, seconds=2)
    roots = [configure(installed, env, tmp_path / name) for name in ("a", "b")]
    running = [
        subprocess.Popen(
            [installed.command, "--repo", root, "--json", "activate"],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for root in roots
    ]
    for process in running:
        stdout, stderr = process.communicate(timeout=600)
        assert process.returncode == 0, (stdout + stderr).decode()
        assert json.loads(stdout)["code"] == "prepared"
    assert installs(log) == 1
    store = Path(env["GATESEAL_INTERPRETER_STORE"]).resolve()
    assert set(os.listdir(store)) == sealed(store)
    for root in roots:
        assert prepared_state(root)["paths"]["interpreter"].startswith(str(store))
        success(["git", "add", *TRACKED], root)
        commit_hygiene(root)


@pytest.mark.parametrize("missing_file", [False, True])
def test_partial_entry_is_replaced_at_the_next_activation(
    installed, tmp_path, missing_file
):
    log = tmp_path / "installs.log"
    env = logging_shim(installed, tmp_path, log)
    store = Path(env["GATESEAL_INTERPRETER_STORE"]).resolve()
    first = adopt(installed, env, tmp_path / "first")
    (store / (SHA + ".json")).unlink()
    removed = entry_file(store, "this.py")
    if missing_file:
        remove_file(removed)
    second = adopt(installed, env, tmp_path / "second")
    assert installs(log) == 2
    assert set(os.listdir(store)) == sealed(store)
    assert removed.is_file()
    assert installed.cli(first, "doctor", env=env)["code"] == "healthy"
    for root in (first, second):
        commit_hygiene(root)


@pytest.mark.parametrize("damage", ["content", "missing file", "closed folder"])
def test_damaged_entry_is_repaired_for_every_repository(installed, tmp_path, damage):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    first = adopt(installed, env, tmp_path / "first")
    second = adopt(installed, env, tmp_path / "second")
    damaged = entry_file(store, "this.py")
    if damage == "content":
        overwrite(damaged, damaged.read_bytes().replace(b"Gur", b"Tur", 1))
    elif damage == "missing file":
        remove_file(damaged)
    else:
        entry_file(store, "turtledemo").chmod(0)
    for root in (first, second):
        assert installed.cli(root, "doctor", code=3, env=env)["code"] == (
            "needs_activation"
        )
    assert installed.cli(second, "activate", env=env)["code"] == "prepared"
    commit_hygiene(first)
    assert installed.cli(first, "doctor", env=env)["code"] == "healthy"
    assert set(os.listdir(store)) == sealed(store)


def test_unreferenced_entries_are_removed(installed, tmp_path):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    root = adopt(installed, env, tmp_path / "repository")
    garbage = plant_garbage(store)
    assert garbage <= set(os.listdir(store))
    assert installed.cli(root, "activate", env=env)["code"] == "already_prepared"
    assert set(os.listdir(store)) == sealed(store)
    assert links(store) == {published(root).name: published(root)}
    commit_hygiene(root)


def test_an_attempt_that_lost_its_link_is_prepared_again(installed, tmp_path):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    root = adopt(installed, env, tmp_path / "repository")
    lost = published(root)
    for link in (store / (SHA + ".refs")).iterdir():
        link.unlink()
    assert installed.cli(root, "activate", env=env)["code"] == "prepared"
    assert published(root) != lost
    assert set(os.listdir(store)) == sealed(store)
    assert links(store) == {published(root).name: published(root)}
    commit_hygiene(root)


def test_failed_activation_removes_the_entry_of_a_deleted_clone(installed, tmp_path):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    deleted = adopt(installed, env, tmp_path / "deleted")
    other = configure(installed, env, tmp_path / "other")
    shutil.rmtree(deleted)
    shims = tmp_path / "old uv"
    shims.mkdir()
    real = shutil.which("uv", path=env["PATH"])
    (shims / "uv").write_text(
        '#!/bin/sh\nif [ "$1" = --version ]; then echo "uv 0.0.1"; exit 0; fi\n'
        f'exec {shlex.quote(str(real))} "$@"\n'
    )
    (shims / "uv").chmod(0o755)
    old = dict(env, PATH=os.pathsep.join([str(shims), env["PATH"]]))
    failed = installed.cli(other, "activate", code=4, env=old)
    assert failed["code"] == "uv_version_mismatch"
    assert set(os.listdir(store)) == {"store.lock"}


def test_collection_waits_for_a_held_store(installed, tmp_path):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    root = adopt(installed, env, tmp_path / "repository")
    garbage = plant_garbage(store)
    descriptor = os.open(store / "store.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        assert installed.cli(root, "activate", env=env)["code"] == "already_prepared"
        assert garbage <= set(os.listdir(store))
    finally:
        os.close(descriptor)
    assert installed.cli(root, "activate", env=env)["code"] == "already_prepared"
    assert set(os.listdir(store)) == sealed(store)


def fake_store(monkeypatch, tmp_path):
    """Point the store at tmp_path/store, with a sealed-looking entry for SHA."""
    store = tmp_path.resolve() / "store"
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", str(store))
    (store / SHA / "bin").mkdir(parents=True)
    (store / (SHA + ".json")).write_text("{}")
    (store / "store.lock").touch()
    return store


def test_only_an_entry_in_this_store_needs_a_link(monkeypatch, tmp_path):
    store = fake_store(monkeypatch, tmp_path)
    attempt = store.parent / "gateseal" / ("attempt-" + "a" * 32)
    attempt.mkdir(parents=True)
    hooks = "b" * 64
    paths = (store / SHA / "bin/python", store / hooks, tmp_path / "attempt/compiler")
    assert not interpreter_store.linked(attempt, paths)
    for entry in (SHA, hooks):
        (store / (entry + ".refs")).mkdir()
        (store / (entry + ".refs") / attempt.name).symlink_to(attempt)
    assert interpreter_store.linked(attempt, paths)
    (store / (SHA + ".refs") / attempt.name).unlink()
    assert not interpreter_store.linked(attempt, paths)
    assert interpreter_store.linked(attempt, paths[1:])
    assert interpreter_store.linked(attempt, (tmp_path / "other/python",))


def test_racing_processes_all_open_a_new_lock(tmp_path):
    """Four processes open one new lock at once, as two first activations do.

    On macOS an O_CREAT open relative to a directory descriptor, as the store
    opens its lock, fails with ENOENT for most of them. The racers are forked,
    so all of them wait on one pipe that a single write releases.
    """
    for round_ in range(5):
        lock = tmp_path / str(round_) / "store.lock"
        lock.parent.mkdir()
        directory = os.open(lock.parent, os.O_RDONLY | os.O_DIRECTORY)
        read, write = os.pipe()
        racers = []
        with warnings.catch_warnings(action="ignore", category=DeprecationWarning):
            for _ in range(4):
                pid = os.fork()
                if pid == 0:
                    code = 1
                    try:
                        os.read(read, 1)
                        os.close(bootstrap.open_lock(lock.name, directory))
                        code = 0
                    finally:
                        os._exit(code)
                racers.append(pid)
        os.close(read)
        os.write(write, b"x" * len(racers))
        os.close(write)
        os.close(directory)
        codes = [os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1]) for pid in racers]
        assert codes == [0] * len(racers), round_


def test_a_lock_is_private_and_never_opened_through_a_link(tmp_path):
    lock = tmp_path / "store.lock"
    os.close(bootstrap.open_lock(lock))
    assert stat.S_IMODE(lock.stat().st_mode) == 0o600
    (tmp_path / "link").symlink_to(lock)
    with pytest.raises(OSError):
        bootstrap.open_lock(tmp_path / "link")
    (tmp_path / "dangling").symlink_to(tmp_path / "missing")
    with pytest.raises(OSError):
        bootstrap.open_lock(tmp_path / "dangling")
    assert not (tmp_path / "missing").exists()


def test_collection_removes_an_entry_whose_folders_are_closed(monkeypatch, tmp_path):
    store = fake_store(monkeypatch, tmp_path)
    closed = store / SHA / "lib" / "closed"
    closed.mkdir(parents=True)
    (closed / "module.py").write_text("")
    closed.chmod(0)
    (store / SHA).chmod(0)
    interpreter_store.collect()
    assert set(os.listdir(store)) == {"store.lock"}


def test_store_location_override(monkeypatch, tmp_path):
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", str(tmp_path / "store"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    assert interpreter_store.location() == tmp_path / "store"


def test_relative_store_override_is_refused(monkeypatch):
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", "relative/store")
    with pytest.raises(GatesealError) as refused:
        interpreter_store.location()
    assert refused.value.code == "unsupported_path"
    assert "GATESEAL_INTERPRETER_STORE" in refused.value.next_step


@pytest.mark.parametrize("data", [None, "", "relative/data"])
def test_store_defaults_to_the_home_data_folder(monkeypatch, tmp_path, data):
    monkeypatch.delenv("GATESEAL_INTERPRETER_STORE")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if data is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", data)
    expected = tmp_path / "home" / ".local/share/gateseal/interpreters"
    assert interpreter_store.location() == expected


def test_store_follows_an_absolute_xdg_data_home(monkeypatch, tmp_path):
    monkeypatch.delenv("GATESEAL_INTERPRETER_STORE")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    expected = tmp_path / "data" / "gateseal" / "interpreters"
    assert interpreter_store.location() == expected
