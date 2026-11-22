"""An in-process gate whose pre-commit is a stand-in, for tests of its CI output.

The real pre-commit needs an install, so a shell script takes the place of its
Python. It records its arguments, prints the output a test gives it, runs the
test's edit of the worktree as a hook would, and exits with the test's status.
The command line, the policy, the worktree and its Git are real.
"""

import contextlib
import io
import os
from dataclasses import dataclass
from pathlib import Path

from conftest import TRACKED
from gateseal import setup
from gateseal.cli import main
from gateseal.worktree import locate_worktree
from installation import success
from stand_ins import INSTALLED, configure, executable

EOF_FAILED = (
    "fix end of files.........................................Failed\n"
    "- hook id: end-of-file-fixer\n"
    "- exit code: 1\n"
)
YAML_PASSED = "check yaml...............................................Passed\n"
WHITESPACE_FIXED = (
    "trim trailing whitespace.................................Failed\n"
    "- hook id: trailing-whitespace\n"
    "- exit code: 1\n"
    "- files were modified by this hook\n"
    "\n"
    "Fixing notes.txt\n"
)
GITHUB = {"GITHUB_ACTIONS": "true"}
AZURE = {"TF_BUILD": "True"}


@dataclass
class StandIn:
    """A configured clone and the stand-in for its pre-commit's Python."""

    root: Path
    python: str
    state: Path

    def answer(self, output, status=0, edit=""):
        """Print output and exit with status; edit is shell run in the clone."""
        (self.state / "output").write_text(output)
        (self.state / "status").write_text(str(status))
        executable(self.state / "edit", f"#!/bin/sh\n{edit}\n")

    def use(self, monkeypatch):
        monkeypatch.setattr(setup, "require_pre_commit", lambda: self.python)
        mise = self.state / "mise bin"
        monkeypatch.setenv("PATH", os.pathsep.join([str(mise), os.environ["PATH"]]))

    def arguments(self):
        return (self.state / "arguments").read_text().splitlines()

    def reset(self):
        success(["git", "checkout", "--", "."], self.root)
        self.answer("")


def stand_in(directory):
    """Return a StandIn for a clone with hygiene, secrets and a tracked notes.txt."""
    root = directory / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], directory)
    configure(locate_worktree(root), ("hygiene", "secrets"))
    (root / "notes.txt").write_text("original\n")
    success(["git", "add", *TRACKED, "notes.txt"], root)
    state = directory / "state"
    python = executable(
        state / "python",
        "#!/bin/sh\n" + INSTALLED + f"printf '%s\\n' \"$@\" > '{state / 'arguments'}'\n"
        f"cat '{state / 'output'}'\n"
        f"'{state / 'edit'}'\n"
        f"exit \"$(cat '{state / 'status'}')\"\n",
    )
    executable(state / "mise bin" / "mise", "#!/bin/sh\n" + INSTALLED)
    found = StandIn(root, str(python), state)
    found.answer("")
    return found


def gateseal(root, *arguments):
    """Return main's exit code and output for arguments in root."""
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), *arguments])
    return code, output.getvalue()
