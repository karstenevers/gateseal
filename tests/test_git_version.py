import os
import shlex
import shutil

import pytest
from conftest import TRACKED, published
from gateseal.diagnostics import GatesealError
from gateseal.runtime import locate_worktree
from installation import process, success

FLOOR = "and gateseal requires Git 2.54.0 or later."
INSTALL = "Install Git 2.54.0 or later, then retry."


def stand_in(directory, reported=None):
    """Write a git into directory that runs the real one, reporting reported."""
    real = shutil.which("git")
    git = directory / "git"
    answer = (
        ""
        if reported is None
        else f'if [ "$1" = --version ]; then echo {shlex.quote(reported)}; exit 0; fi\n'
    )
    git.write_text(f'#!/bin/sh\n{answer}exec {shlex.quote(real)} "$@"\n')
    git.chmod(0o755)
    return git


def per_folder(directory, root):
    """Write a git that, as a mise or asdf shim can, reports 2.54.0 only in root."""
    real = shutil.which("git")
    git = directory / "git"
    inside = shlex.quote(str(root.resolve()))
    git.write_text(
        '#!/bin/sh\nif [ "$1" = --version ]; then\n'
        f'  case "$(pwd -P)/" in {inside}/*) echo "git version 2.54.0" ;;\n'
        '  *) echo "git version 2.43.0" ;; esac\n  exit 0\nfi\n'
        f'exec {shlex.quote(real)} "$@"\n'
    )
    git.chmod(0o755)
    return git


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], tmp_path)
    return root


@pytest.fixture
def shims(tmp_path, monkeypatch):
    directory = tmp_path / "git shims"
    directory.mkdir()
    monkeypatch.setenv("PATH", os.pathsep.join([str(directory), os.environ["PATH"]]))
    return directory


@pytest.mark.parametrize(
    "reported",
    [
        "git version 2.54.0",
        "git version 2.54.0 (Apple Git-157)",
        "git version 2.56.0.rc1",
        "git version 10.0.0",
    ],
)
def test_git_from_the_floor_locates_the_worktree(repository, shims, reported):
    git = stand_in(shims, reported)
    assert locate_worktree(repository).git == str(git)


@pytest.mark.parametrize(
    ("reported", "found"),
    [
        ("git version 2.53.9", "Git 2.53.9"),
        ("git version 2.43.0", "Git 2.43.0"),
        ("git version 2.9.5", "Git 2.9.5"),
        ("", "no Git version"),
        ("hub version 2.14.2", "no Git version"),
    ],
)
def test_older_git_stops_a_worktree_command(repository, shims, reported, found):
    git = stand_in(shims, reported)
    with pytest.raises(GatesealError) as failure:
        locate_worktree(repository)
    error = failure.value
    assert error.code == "git_version_mismatch"
    assert error.exit_code == 4
    assert str(error) == f"{git} reports {found}, {FLOOR}"
    assert error.next_step == INSTALL


def test_a_per_folder_git_is_asked_in_the_repository(
    repository, shims, tmp_path, monkeypatch
):
    git = per_folder(shims, repository)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert locate_worktree(repository).git == str(git)


def test_older_git_stops_commands_and_commits_until_replaced(
    installed, configured, tmp_path
):
    shims = tmp_path / "git shims"
    shims.mkdir()
    git = stand_in(shims)
    path = os.pathsep.join([str(shims), installed.environment["PATH"]])
    shimmed = dict(installed.environment, PATH=path)
    assert installed.cli(configured, "activate", env=shimmed)["code"] == "prepared"
    attempt = published(configured)

    stand_in(shims, "git version 2.43.0")
    message = f"{git} reports Git 2.43.0, {FLOOR}"
    for command in (
        "configure",
        "check",
        "activate",
        "run",
        "gate",
        "status",
        "doctor",
    ):
        result = installed.cli(configured, command, code=4, env=shimmed)
        assert result["code"] == "git_version_mismatch", command
        assert result["diagnostics"][0]["message"] == message
        assert result["next_steps"] == [INSTALL]
    assert published(configured) == attempt
    success(["git", "add", *TRACKED], configured)
    committed = process(["git", "commit", "-m", "Adopt hygiene"], configured)
    assert committed.returncode != 0
    assert (
        f"gateseal: {message}\n"
        "Next: Install Git 2.54.0 or later, run gateseal activate, then retry.\n"
    ) in committed.stderr

    status = installed.cli(configured, "status")
    assert status["code"] == "needs_activation"
    assert status["diagnostics"][0]["message"] == message
    assert installed.cli(configured, "doctor", code=3)["code"] == "needs_activation"
    assert installed.cli(configured, "activate")["code"] == "prepared"
    success(["git", "commit", "-m", "Adopt hygiene"], configured)
    assert installed.cli(configured, "doctor")["code"] == "healthy"
