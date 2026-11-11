import os
import stat
from pathlib import Path

import pytest
from conftest import (
    commit_hygiene,
    overwrite,
    prepared_state,
    published,
    remove_file,
)
from installation import process, success
from test_interpreter_store import (
    adopt,
    configure,
    hook_entries,
    sealed,
    store_environment,
)


def environments(root):
    return set(prepared_state(root)["hook_environments"])


def test_clones_and_worktrees_share_each_hook_environment(installed, tmp_path):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    first = adopt(installed, env, tmp_path / "first")
    (entry,) = hook_entries(store)
    seal = (store / (entry + ".json")).stat()
    second = adopt(installed, env, tmp_path / "second")
    worktree = tmp_path / "worktree ü"
    success(["git", "worktree", "add", "-q", "-b", "next", worktree], first, env)
    assert installed.cli(worktree, "activate", env=env)["code"] == "prepared"
    assert set(os.listdir(store)) == sealed(store)
    assert (store / (entry + ".json")).stat().st_mtime_ns == seal.st_mtime_ns
    roots = (first, second, worktree)
    shared = environments(first)
    assert all(environments(root) == shared for root in roots)
    assert all(Path(path).is_relative_to(store / entry) for path in shared)
    assert set(os.listdir(store / (entry + ".refs"))) == {
        published(root).name for root in roots
    }
    for root in roots:
        assert not list((published(root) / "pre-commit-home").glob("repo*"))
    for directory, directories, files in os.walk(store / entry):
        for name in (".", *directories, *files):
            info = os.lstat(os.path.join(directory, name))
            if not stat.S_ISLNK(info.st_mode):
                assert not info.st_mode & 0o222, os.path.join(directory, name)
    for root in roots:
        commit_hygiene(root, env)


@pytest.mark.parametrize("damage", ["seal", "missing file", "content"])
def test_a_rebuilt_hook_entry_stops_other_commits_until_they_activate(
    installed, tmp_path, damage
):
    store = (tmp_path / "store").resolve()
    env = store_environment(installed, store)
    first = adopt(installed, env, tmp_path / "first")
    (entry,) = hook_entries(store)
    before = environments(first)
    (environment,) = (Path(path) for path in before)
    marker = environment / ".install_state_v1"
    if damage == "seal":
        (store / (entry + ".json")).unlink()
    elif damage == "missing file":
        remove_file(marker)
    else:
        overwrite(marker, marker.read_bytes().replace(b"[", b"{", 1))
    second = adopt(installed, env, tmp_path / "second")
    assert hook_entries(store) == {entry}
    assert environments(second) != before
    assert installed.cli(first, "doctor", code=3, env=env)["code"] == (
        "needs_activation"
    )
    stopped = process(["git", "commit", "--allow-empty", "-m", "Stale"], first, env)
    assert stopped.returncode != 0
    assert "Run gateseal activate" in stopped.stdout + stopped.stderr
    assert installed.cli(first, "activate", env=env)["code"] == "prepared"
    assert environments(first) == environments(second)
    assert set(os.listdir(store)) == sealed(store)
    for root in (first, second):
        commit_hygiene(root, env)


@pytest.mark.parametrize("name", ["$HOME", "it's", "a:b"])
def test_a_store_path_that_hook_launchers_misread_is_refused(installed, tmp_path, name):
    store = tmp_path / f"store {name}"
    env = store_environment(installed, store)
    root = configure(installed, env, tmp_path)
    refused = installed.cli(root, "activate", code=2, env=env)
    assert refused["code"] == "unsupported_path"
    assert refused["diagnostics"][0]["message"].startswith(
        f"The interpreter store {store} has "
    )
    assert refused["next_steps"][0].startswith("Set GATESEAL_INTERPRETER_STORE to")
    assert not store.exists()
    assert not (root / ".git/gateseal/current").is_symlink()
