"""Upgrade a real consumer's compiler with a candidate that misbehaves.

No real candidate misbehaves, so a stub stands as the candidate's Python at
its process boundary. It passes configure to this compiler, adds the outputs
it is told to claim to the Lock that configure wrote, and answers check as it
is told to. It replaces candidate.installed, whose uv install would download
the candidate.
"""

import contextlib
import json
import shlex
import sys

import pytest
from gateseal import __version__, candidate, upgrade
from test_candidate_commands import ARTIFACT, SOURCE
from test_upgrade_worktree import configure, refused

STUB = """
import hashlib, json, subprocess, sys
from pathlib import Path

arguments = sys.argv[1:]
if arguments[arguments.index("--json") + 1] == "check":
    print({answer!r})
    sys.exit({status})
configured = subprocess.run([sys.executable, *arguments]).returncode
repository = Path(arguments[arguments.index("--repo") + 1])
lock = repository / "gateseal.lock"
for name in {claims!r}:
    document = json.loads(lock.read_bytes())
    digest = hashlib.sha256((repository / name).read_bytes()).hexdigest()
    document["outputs"][name] = {{"sha256": digest}}
    lock.write_text(json.dumps(document))
sys.exit(configured)
"""


def check_answer(status, code, severity, message, next_steps=()):
    """Return a check result in the shape this compiler's check prints."""
    return json.dumps(
        {
            "schema": 1,
            "command": "check",
            "status": status,
            "code": code,
            "changed": [],
            "diagnostics": [
                {"severity": severity, "category": code, "message": message}
            ],
            "next_steps": list(next_steps),
        }
    )


CONSISTENT = check_answer(
    "ok", "consistent", "info", "Manifest, lock, and native policy agree."
)
MISMATCH = check_answer(
    "failed",
    "lock_mismatch",
    "error",
    "Lock inputs or ownership disagree with the selected policy.",
    ["gateseal configure"],
)
UPGRADE = upgrade.Request(None, (None, SOURCE, ARTIFACT.sha256), {}, None, None, False)


def install_stub(tmp_path, monkeypatch, answer, status, claims=()):
    """Make the stub the candidate that every upgrade in this test installs."""
    code = STUB.format(answer=answer, status=status, claims=claims)
    python = tmp_path / "candidate-python"
    python.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)} "$@"\n'
    )
    python.chmod(0o755)

    @contextlib.contextmanager
    def installed(artifact, root):
        yield candidate.Candidate(str(python), artifact)

    monkeypatch.setattr(candidate, "installed", installed)


def test_a_candidate_whose_check_refuses_its_own_configure_writes_nothing(
    consumer, tmp_path, monkeypatch
):
    install_stub(tmp_path, monkeypatch, MISMATCH, 2)
    error = refused(configure(consumer, "hygiene"), UPGRADE)
    assert (error.code, error.exit_code) == ("candidate_rejected", 2)
    named = (__version__, "check", "lock_mismatch")
    assert all(value in str(error) for value in named)
    assert error.next_step == "gateseal configure"


@pytest.mark.parametrize("claim", ["gateseal.toml", ".git/config"])
def test_a_candidate_lock_that_claims_the_manifest_or_a_git_file_writes_nothing(
    consumer, tmp_path, monkeypatch, claim
):
    install_stub(tmp_path, monkeypatch, CONSISTENT, 0, (claim,))
    error = refused(configure(consumer, "hygiene"), UPGRADE)
    assert (error.code, error.exit_code) == ("candidate_rejected", 2)
    assert all(value in str(error) for value in (__version__, claim))
