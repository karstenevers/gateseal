"""A developer adds hooks of their own through Git, without a gateseal file.

In a configured repository, Git runs a hook from its configuration and then
the commit launcher, whose pre-commit runs a pre-commit.legacy script before
the policy's hooks. Neither touches a file that gateseal wrote.
"""

import os

import pytest
from conftest import git_path
from installation import process, success

pytestmark = pytest.mark.integration

LOG = "personal-hooks.log"


def test_personal_hooks_run_before_the_policy_hooks(installed, adopted):
    log = git_path(adopted, LOG)
    success(["git", "config", "hook.audit.event", "pre-commit"], adopted)
    success(["git", "config", "hook.audit.command", f'echo config >> "{log}"'], adopted)
    legacy = git_path(adopted, "hooks/pre-commit.legacy")
    legacy.write_text(f'#!/bin/sh\necho legacy >> "{log}"\n')
    os.chmod(legacy, 0o755)
    (adopted / "notes.txt").write_bytes(b"trailing space  \n")
    success(["git", "add", "notes.txt"], adopted)
    failed = process(["git", "commit", "-m", "Bad hygiene"], adopted)
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert log.read_text() == "config\nlegacy\n"
    assert (adopted / "notes.txt").read_bytes() == b"trailing space\n"
    success(["git", "add", "notes.txt"], adopted)
    success(["git", "commit", "-m", "Fixed hygiene"], adopted)
    assert log.read_text() == "config\nlegacy\n" * 2
    assert installed.cli(adopted, "check")["code"] == "consistent"
    assert success(["git", "status", "--porcelain"], adopted).stdout == ""
