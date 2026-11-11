"""What activation checks and selects before it creates an attempt or downloads.

Most tests run the uv on PATH, which answers offline. A uv that reports another
release stands in for an older or newer install.
"""

import hashlib
import os
import platform
import shutil
import zipfile
from pathlib import Path

import pytest
from gateseal import __version__, lock, runtime
from gateseal.compiler_release import load_runtime
from gateseal.diagnostics import GatesealError
from installation import clone

SOURCE = (
    "https://raw.githubusercontent.com/example/wheels/"
    f"{'0' * 40}/artifacts/gateseal-{__version__}-py3-none-any.whl"
)
MINIMUM = load_runtime().minimum_uv


def configure(root, wheel=None):
    worktree = runtime.locate_worktree(root)
    local = runtime.preparation_directory(worktree, create=True)
    checksum = hashlib.sha256(wheel.read_bytes()).hexdigest() if wheel else "a" * 64
    lock.configure(
        worktree.root,
        worktree.git,
        local,
        ["hygiene"],
        lock.PolicyRequest(),
        str(wheel) if wheel else None,
        None if wheel else SOURCE,
        checksum,
        None,
    )
    return worktree, lock.load(worktree.root, local)


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    directory = tmp_path_factory.mktemp("template")
    configure(clone(directory / "origin", directory / "consumer"))
    return directory / "consumer"


@pytest.fixture
def configured(template, tmp_path, monkeypatch):
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv cache"))
    monkeypatch.delenv("UV_PYTHON_CACHE_DIR", raising=False)
    shutil.copytree(template, tmp_path / "consumer", symlinks=True)
    worktree = runtime.locate_worktree(tmp_path / "consumer")
    return worktree, lock.load(worktree.root, worktree.preparation)


def wheel(directory):
    """Write a compiler wheel whose metadata names this release, and nothing else."""
    path = directory / f"gateseal-{__version__}-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"gateseal-{__version__}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: gateseal\nVersion: {__version__}\n",
        )
    return path


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


def test_a_plan_names_uv_its_archive_cache_and_the_locked_compiler(
    configured, tmp_path
):
    worktree, locked = configured
    plan = runtime._plan(locked, Path(worktree.git), None)
    assert plan.uv == str(Path(shutil.which("uv")).absolute())
    assert plan.environment["UV_PYTHON_CACHE_DIR"] == str(
        tmp_path / "uv cache" / "gateseal-python-archives"
    )
    assert plan.compiler_source == SOURCE
    assert (plan.hook_python, plan.mise) == (None, None)


def test_a_local_wheel_is_verified_and_installed_from_its_resolved_file(tmp_path):
    locked_wheel = wheel(tmp_path)
    root = clone(tmp_path / "origin", tmp_path / "consumer")
    worktree, locked = configure(root, locked_wheel)
    plan = runtime._plan(locked, Path(worktree.git), None)
    assert plan.compiler_source == locked_wheel.as_uri()
    (tmp_path / "override").mkdir()
    override = wheel(tmp_path / "override")
    plan = runtime._plan(locked, Path(worktree.git), override)
    assert plan.compiler_source == override.as_uri()
    locked_wheel.unlink()
    error = refused(runtime._plan, locked, Path(worktree.git), None)
    assert (error.code, error.exit_code) == ("missing_artifact", 4)


@pytest.fixture
def reported(tmp_path, monkeypatch):
    """Return a setter for the release a uv first on PATH reports."""
    real = shutil.which("uv")
    shim = tmp_path / "uv shim" / "uv"
    shim.parent.mkdir()
    monkeypatch.setenv("PATH", f"{shim.parent}{os.pathsep}{os.environ['PATH']}")

    def report(line):
        shim.write_text(
            f'#!/bin/sh\nif [ "$1" = --version ]; then echo "{line}"; exit 0; fi\n'
            f'exec "{real}" "$@"\n'
        )
        shim.chmod(0o755)

    return report


def test_the_minimum_uv_release_is_accepted(configured, reported):
    worktree, locked = configured
    reported(f"uv {MINIMUM} (stand-in)")
    plan = runtime._plan(locked, Path(worktree.git), None)
    assert Path(plan.uv).parent.name == "uv shim"


@pytest.mark.parametrize("line", ["uv 0.0.1 (stand-in)", "uv", "uv unknown"])
def test_an_older_or_unreadable_uv_release_is_refused(configured, reported, line):
    worktree, locked = configured
    reported(line)
    error = refused(runtime._plan, locked, Path(worktree.git), None)
    assert (error.code, error.exit_code) == ("uv_version_mismatch", 4)
    assert all(value in str(error) for value in (__version__, MINIMUM))


def test_an_untested_machine_is_refused(configured, monkeypatch):
    worktree, locked = configured
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    error = refused(runtime._plan, locked, Path(worktree.git), None)
    assert error.code == "unsupported_platform"
