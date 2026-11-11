"""gateseal run shows long hook output in part, and in full with --verbose.

A real attempt needs downloads, so the clone publishes a stand-in attempt
whose pre-commit is a shell script that prints more output than a result
shows. The command line, the policy, the pinning and the process are real.
"""

import contextlib
import io

import pytest
from conftest import TRACKED
from gateseal import interpreter_store, runtime
from gateseal.cli import main
from gateseal.lock import load
from installation import success
from prepared_attempts import configure, prepare

OUTPUT = "".join(f"check {number:04}.........Passed\n" for number in range(400))


@pytest.fixture(scope="module")
def clone(tmp_path_factory):
    """A configured clone whose published attempt's pre-commit prints OUTPUT."""
    directory = tmp_path_factory.mktemp("verbose")
    root = directory / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], directory)
    worktree = runtime.locate_worktree(root)
    configure(worktree, ("hygiene",))
    success(["git", "add", *TRACKED], root)
    (directory / "output").write_text(OUTPUT)
    script = f"#!/bin/sh\ncat '{directory / 'output'}'\n"
    locked = load(worktree.root, runtime.preparation_directory(worktree, create=True))
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(interpreter_store.STORE_VARIABLE, str(directory / "store"))
        prepare(worktree, locked, pre_commit=script)
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
