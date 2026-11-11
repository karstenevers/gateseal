import dataclasses
import hashlib
import os
import shutil
from pathlib import Path

import pytest
from conftest import overwrite
from gateseal import bootstrap
from test_bootstrap_record import prepare

FIXED_NS = 946_684_800 * 1_000_000_000


def refused(record, *names):
    """Check that a commit stops on record, naming each of names, until activation."""
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.verify_runtime(record, content=False)
    assert all(name in str(raised.value) for name in names)
    assert raised.value.next_step == bootstrap.ACTIVATE


def sealed_tree(root):
    """Write a tree with a directory, a link to it, and two files of fixed mtime."""
    (root / "bin").mkdir(parents=True)
    (root / "bin").chmod(0o755)
    (root / "bin/tool").write_bytes(b"tool\n")
    (root / "bin/tool").chmod(0o755)
    (root / "lib").symlink_to("bin")
    (root / "ü.txt").write_bytes(b"text\n")
    (root / "ü.txt").chmod(0o644)
    for name in ("bin/tool", "ü.txt"):
        os.utime(root / name, ns=(FIXED_NS, FIXED_NS))
    return root


def v1_digest(records):
    """Return the v1 seal of records, each one entry's JSON array in walk order."""
    hasher = hashlib.sha256(b"gateseal-runtime-tree-v1\0")
    for record in records:
        data = record.encode()
        hasher.update(len(data).to_bytes(8, "big") + data)
    return hasher.hexdigest()


@pytest.mark.parametrize("content", [False, True], ids=["metadata", "content"])
def test_a_seal_is_the_v1_digest_that_every_release_computes(tmp_path, content):
    """Every release must compute the same seal of the same tree.

    The machine-wide store keeps the seals one release wrote, and a release
    that computes another digest rebuilds the entry under every other one.
    """
    root = sealed_tree(tmp_path / "entry")
    link = os.lstat(root / "lib").st_mode & 0o7777
    if content:
        tool = "file:" + hashlib.sha256(b"tool\n").hexdigest()
        text = "file:" + hashlib.sha256(b"text\n").hexdigest()
    else:
        tool = text = f"file:5:{FIXED_NS}"
    expected = v1_digest(
        [
            '["bin",493,"directory"]',
            f'["lib",{link},"link:bin"]',
            f'["ü.txt",420,"{text}"]',
            f'["bin/tool",493,"{tool}"]',
        ]
    )
    assert bootstrap.tree_digest(root, (root,), content=content) == expected


def test_a_link_may_point_only_into_a_sealed_tree(tmp_path):
    store, other = tmp_path / "store", sealed_tree(tmp_path / "other")
    store.mkdir()
    (store / "python").symlink_to(other / "bin/tool")
    assert bootstrap.tree_digest(store, (store, other), content=True)
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.tree_digest(store, (store,), content=True)


def test_a_dangling_link_breaks_the_seal(tmp_path):
    root = sealed_tree(tmp_path / "entry")
    (root / "gone").symlink_to("bin/removed")
    with pytest.raises(FileNotFoundError):
        bootstrap.tree_digest(root, (root,), content=False)


@pytest.mark.parametrize("replacement", ["missing", "link"])
def test_a_missing_or_linked_tree_has_no_seal(tmp_path, replacement):
    root = sealed_tree(tmp_path / "entry")
    path = tmp_path / replacement
    if replacement == "link":
        path.symlink_to(root)
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.tree_digest(path, (root,), content=False)


def test_a_commit_checks_metadata_and_doctor_checks_content_too(tmp_path):
    record = prepare(tmp_path, tmp_path)
    bootstrap.verify_runtime(record, content=True)
    overwrite(record.paths.compiler_python, b"#!/bin/ok\n")
    bootstrap.verify_runtime(record, content=False)
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.verify_runtime(record, content=True)
    assert raised.value.next_step == bootstrap.ACTIVATE


@pytest.mark.parametrize("content", [False, True], ids=["commit", "doctor"])
def test_a_new_mtime_breaks_the_seal(tmp_path, content):
    record = prepare(tmp_path, tmp_path)
    os.utime(record.paths.pre_commit_python, ns=(FIXED_NS, FIXED_NS))
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.verify_runtime(record, content=content)
    assert raised.value.next_step == bootstrap.ACTIVATE


def test_a_program_that_reports_no_git_version_is_refused(tmp_path):
    record = prepare(tmp_path, tmp_path)
    true = Path(shutil.which("true"))
    paths = dataclasses.replace(record.paths, git=true)
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.verify_runtime(
            dataclasses.replace(record, paths=paths), content=False
        )
    assert str(true) in str(raised.value)
    assert "no Git version" in str(raised.value)
    assert bootstrap.MINIMUM_GIT in raised.value.next_step
    assert "gateseal activate" in raised.value.next_step


def test_a_linked_tools_directory_is_refused(tmp_path):
    record = prepare(tmp_path, tmp_path)
    copy = tmp_path / "tools copy"
    record.paths.tools.rename(copy)
    record.paths.tools.symlink_to(copy)
    refused(record, str(record.paths.tools))


def hook_environment(record):
    ((path, environment),) = record.hook_environments.items()
    return path, environment


@pytest.mark.parametrize("missing", ["python", "marker"])
def test_a_hook_environment_without_its_python_or_marker_is_missing(tmp_path, missing):
    record = prepare(tmp_path, tmp_path)
    path, environment = hook_environment(record)
    if missing == "python":
        environment.python.unlink()
    else:
        (path / ".install_state_v1").unlink()
    refused(record, "hook environment", "missing")


def test_a_changed_installation_record_is_refused(tmp_path):
    record = prepare(tmp_path, tmp_path)
    path, _ = hook_environment(record)
    (path / ".install_state_v1").write_text('{"additional_dependencies": ["x"]}')
    refused(record, "installation record")


@pytest.mark.parametrize("damage", ["missing", "not executable"])
def test_a_hook_executable_must_be_runnable(tmp_path, damage):
    record = prepare(tmp_path, tmp_path)
    _, environment = hook_environment(record)
    (executable,) = environment.executables
    if damage == "missing":
        executable.unlink()
    else:
        executable.chmod(0o644)
    refused(record, str(executable))


def test_tools_run_without_the_callers_python_or_uv_settings(monkeypatch):
    dropped = {
        "PYTHONPATH": "/x",
        "PYTHONHOME": "/x",
        "UV_CACHE_DIR": "/x",
        "PIP_INDEX_URL": "https://example.invalid",
        "VIRTUALENV_PYTHON": "/x",
        "CONDA_PREFIX": "/x",
        "DYLD_INSERT_LIBRARIES": "/x",
        "MISE_ENV": "x",
        "VIRTUAL_ENV": "/x",
        "__PYVENV_LAUNCHER__": "/x",
        "LD_PRELOAD": "/x",
        "LD_LIBRARY_PATH": "/x",
    }
    kept = {"GIT_DIR": "/g", "GIT_INDEX_FILE": "/i", "LANG": "C.UTF-8"}
    for key, value in {**dropped, **kept, "UV_OFFLINE": "0"}.items():
        monkeypatch.setenv(key, value)
    environment = bootstrap.clean_environment()
    assert not dropped.keys() & environment.keys()
    assert kept.items() <= environment.items()
    assert {
        key: environment[key]
        for key in ("UV_OFFLINE", "UV_PYTHON_DOWNLOADS", "PYTHONDONTWRITEBYTECODE")
    } == {
        "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
