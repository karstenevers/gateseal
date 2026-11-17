"""The gateseal-check hook compares what a commit records with the staged Lock.

In this process the hook reads a configured clone's real index through Git.
The installed tests commit through pre-commit, which builds the hook from the
stand-in for gateseal's repository at the commit that the Lock pins.
"""

import json
import os
import re
import shutil
from pathlib import Path

import pytest
from conftest import TRACKED, published_origin, stderr_refusal
from gateseal import __version__, commit_check
from gateseal.compiler_release import PRE_COMMIT, REPOSITORY
from gateseal.diagnostics import NEXT_STEPS
from gateseal.worktree import locate_worktree
from installation import clone, process, success
from stand_ins import configure

CONFIG = ".pre-commit-config.yaml"
STAGE_ALL = (
    "Stage gateseal.toml, gateseal.lock and the files gateseal wrote, then commit "
    "again."
)


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A clone whose index holds the files that configure wrote, as the cwd."""
    root = clone(tmp_path / "origin", tmp_path / "consumer ü")
    configure(locate_worktree(root), ["hygiene"])
    success(["git", "add", *TRACKED], root)
    monkeypatch.chdir(root)
    return root


def hook(capsys):
    code = commit_check.main()
    err = capsys.readouterr().err
    return code, stderr_refusal(err) if code else err


def stage(root, name, edit):
    path = root / name
    path.write_text(edit(path.read_text()))
    success(["git", "add", name], root)


def test_the_staged_files_gateseal_wrote_pass(staged, capsys):
    assert hook(capsys) == (0, "")


def test_an_edited_owned_file_fails_only_once_it_is_staged(staged, capsys):
    (staged / CONFIG).write_text("repos: []\n")
    assert hook(capsys) == (0, "")
    success(["git", "add", CONFIG], staged)
    assert hook(capsys) == (
        1,
        (
            f"The staged {CONFIG} differs from the file gateseal wrote.",
            f"Undo the edit to {CONFIG}, or change gateseal.toml and run gateseal "
            f"configure. {STAGE_ALL}",
        ),
    )


def test_a_manifest_that_configure_did_not_compile_fails(staged, capsys):
    stage(staged, "gateseal.toml", lambda text: text + "# reviewed\n")
    assert hook(capsys) == (
        1,
        (
            "The staged gateseal.toml is not the one that gateseal.lock was compiled "
            "from.",
            f"Run gateseal configure. {STAGE_ALL}",
        ),
    )


def test_a_commit_without_an_owned_file_fails(staged, capsys):
    success(["git", "rm", "--cached", "--quiet", CONFIG], staged)
    assert hook(capsys) == (
        1,
        (f"This commit has no {CONFIG}.", f"Run gateseal configure. {STAGE_ALL}"),
    )


def test_the_hook_reads_the_index_that_git_names(staged, capsys, monkeypatch):
    other = staged / ".git" / "other-index"
    environment = {"GIT_INDEX_FILE": str(other)}
    success(["git", "read-tree", "--empty"], staged, environment)
    monkeypatch.setenv("GIT_INDEX_FILE", str(other))
    assert hook(capsys)[1][0] == "This commit has no gateseal.lock."
    monkeypatch.delenv("GIT_INDEX_FILE")
    assert hook(capsys) == (0, "")


def test_a_refusal_without_its_own_step_names_the_default(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert hook(capsys) == (
        1,
        (
            "Install the documented git prerequisite.",
            NEXT_STEPS["missing_prerequisite"],
        ),
    )


def edit_lock(root, change):
    def edited(text):
        document = json.loads(text)
        change(document)
        return json.dumps(document)

    stage(root, "gateseal.lock", edited)


def test_a_lock_of_another_gateseal_names_it(staged, capsys):
    edit_lock(staged, lambda lock: lock["gateseal"].update(version="9.9.9"))
    code, (message, step) = hook(capsys)
    assert (code, message.split(" from ")[0]) == (
        1,
        "gateseal.lock pins gateseal 9.9.9",
    )
    assert step.startswith(
        "Run gateseal 9.9.9 with uvx gateseal@9.9.9, or run gateseal upgrade"
    )


def test_a_lock_that_lists_a_file_gateseal_never_writes_fails(staged, capsys):
    edit_lock(
        staged, lambda lock: lock["outputs"].update({"notes.txt": {"sha256": "0" * 64}})
    )
    code, (message, step) = hook(capsys)
    assert (code, message) == (
        1,
        "gateseal.lock lists notes.txt as a file gateseal wrote, but gateseal "
        f"{__version__} never writes it.",
    )
    assert step == "Restore gateseal.lock from Git, then run gateseal configure."


def test_an_unreadable_ledger_names_the_field(staged, capsys):
    edit_lock(staged, lambda lock: lock["outputs"][CONFIG].update(sha256="edited"))
    assert hook(capsys)[1][0] == (
        f"Cannot read gateseal.lock: outputs.{CONFIG}.sha256 must be a lowercase "
        "SHA-256."
    )


def test_a_commit_that_stages_an_edited_owned_file_fails(installed, adopted):
    """pre-commit builds the hook at the Lock's commit and runs it first."""
    config = adopted / CONFIG
    original = config.read_text()
    config.write_text(original + "# local\n")
    success(["git", "add", CONFIG], adopted)
    refused = process(["git", "commit", "-m", "Edit an owned file"], adopted)
    shown = refused.stdout + refused.stderr
    assert refused.returncode == 1, shown
    assert re.search(r"Check files and tools against gateseal\.lock\.+Failed", shown), (
        shown
    )
    assert f"gateseal: The staged {CONFIG} differs from the file gateseal wrote." in (
        shown
    )
    assert "run gateseal configure" in shown
    config.write_text(original)
    success(["git", "add", CONFIG], adopted)
    (adopted / "notes.txt").write_text("changed\n")
    committed = success(["git", "commit", "-am", "Change notes"], adopted)
    assert (
        "Check files and tools against gateseal.lock"
        in committed.stdout + committed.stderr
    )


def test_gateseal_run_needs_no_staged_configuration_and_keeps_skip(
    installed, configured
):
    """The hook reads the index that a commit records, so gateseal run skips it.

    gateseal run keeps the hooks that a developer skips with SKIP.
    """
    assert installed.cli(configured, "run")["code"] == "checks_passed"
    (configured / "notes.txt").write_text("notes  \n")
    success(["git", "add", "notes.txt"], configured)
    skipping = dict(installed.environment, SKIP="trailing-whitespace")
    assert installed.cli(configured, "run", env=skipping)["code"] == "checks_passed"
    assert installed.cli(configured, "run", code=1)["code"] == "checks_failed"


@pytest.mark.parametrize("python", ["3.11", "3.12"])
def test_a_developers_own_pre_commit_runs_the_hook_without_gateseal(
    installed, gateseal_origin, tmp_path, python
):
    """A clone commits with pre-commit from a developer's own Python.

    pre-commit builds the hook on that Python. No gateseal is on the PATH.
    """
    origin = published_origin(installed, tmp_path)
    home = tmp_path / "developer ü"
    home.mkdir()
    path = os.pathsep.join(
        entry
        for entry in os.environ["PATH"].split(os.pathsep)
        if not (Path(entry) / "gateseal").exists()
    )
    assert shutil.which("gateseal", path=path) is None
    env = dict(
        os.environ,
        HOME=str(home),
        GIT_CONFIG_GLOBAL=str(home / ".gitconfig"),
        PRE_COMMIT_HOME=str(home / "pre-commit home"),
        PATH=path,
    )
    git_config = ["git", "config", "--global"]
    success(
        [*git_config, f"url.{gateseal_origin.as_uri()}.insteadOf", REPOSITORY],
        home,
        env,
    )
    success([*git_config, "user.name", "Developer"], home, env)
    success([*git_config, "user.email", "developer@example.invalid"], home, env)
    tools = home / "own pre-commit"
    success(["uv", "venv", "--quiet", "--python", python, tools], home, env)
    success(
        ["uv", "pip", "install", "--quiet", "--python", tools / "bin" / "python"]
        + [f"pre-commit=={PRE_COMMIT}"],
        home,
        env,
    )
    clone = home / "clone"
    success(["git", "clone", "--quiet", origin, clone], home, env)
    success([tools / "bin" / "pre-commit", "install"], clone, env)
    (clone / "notes.txt").write_text("notes\n")
    success(["git", "add", "notes.txt"], clone, env)
    committed = success(["git", "commit", "-m", "Add notes"], clone, env)
    shown = committed.stdout + committed.stderr
    assert re.search(r"Check files and tools against gateseal\.lock\.+Passed", shown), (
        shown
    )
    [hook] = (home / "pre-commit home").glob("repo*/py_env-*/bin/gateseal-check")
    version = success([hook.parent / "python", "--version"], clone, env).stdout
    assert version.startswith(f"Python {python}."), version
