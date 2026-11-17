"""gateseal run shows long hook output in part, and in full with --verbose.

The real pre-commit needs an install, so a shell script that prints more
output than a result shows takes its place. The command line, the policy and
the process are real.
"""

import contextlib
import io

import pytest
from conftest import TRACKED
from gateseal import setup
from gateseal.cli import main
from gateseal.worktree import locate_worktree
from installation import success
from stand_ins import configure

OUTPUT = "".join(f"check {number:04}.........Passed\n" for number in range(400))


@pytest.fixture(scope="module")
def configured(tmp_path_factory):
    """A configured clone, and a stand-in for pre-commit's Python that prints OUTPUT."""
    directory = tmp_path_factory.mktemp("verbose")
    root = directory / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], directory)
    configure(locate_worktree(root), ("hygiene",))
    success(["git", "add", *TRACKED], root)
    (directory / "output").write_text(OUTPUT)
    python = directory / "python"
    python.write_text(f"#!/bin/sh\ncat '{directory / 'output'}'\n")
    python.chmod(0o755)
    return root, str(python)


@pytest.fixture
def clone(configured, monkeypatch):
    root, python = configured
    monkeypatch.setattr(setup, "require_pre_commit", lambda: python)
    return root


def run(root, *flags):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), *flags, "run"])
    return code, output.getvalue()


def test_run_cuts_long_hook_output_to_its_start(clone):
    assert len(OUTPUT) > 6000
    code, shown = run(clone)
    assert code == 0
    assert OUTPUT[:6000] in shown
    assert OUTPUT[:6001] not in shown
    assert "--verbose" in shown.partition(OUTPUT[:6000])[2]


def test_verbose_run_shows_all_hook_output(clone):
    assert run(clone, "--verbose") == (0, "Checks passed.\n" + OUTPUT + "\n")
