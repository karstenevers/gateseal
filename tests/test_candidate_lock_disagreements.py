"""A candidate whose Lock disagrees with the files and compiler it configured.

No real candidate writes such a Lock, so a stub stands as the candidate's
Python at its process boundary, as in test_candidate_misbehaving. It runs this
compiler's configure, sets one field of the Lock that configure wrote, and
answers check as consistent.
"""

import shlex
import sys

import pytest
from gateseal import __version__, candidate
from gateseal.diagnostics import GatesealError
from gateseal.processes import find_git
from test_candidate_commands import ARTIFACT, HYGIENE
from test_candidate_misbehaving import CONSISTENT

STUB = """
import json, subprocess, sys
from pathlib import Path

arguments = sys.argv[1:]
if arguments[arguments.index("--json") + 1] == "check":
    print({answer!r})
    sys.exit(0)
configured = subprocess.run([sys.executable, *arguments]).returncode
lock = Path(arguments[arguments.index("--repo") + 1]) / "gateseal.lock"
document = json.loads(lock.read_bytes())
*parents, last = {path!r}
record = document
for key in parents:
    record = record[key]
record[last] = {value!r}
lock.write_text(json.dumps(document))
sys.exit(configured)
"""


def disagreement(tmp_path, path, value):
    """Configure HYGIENE with a candidate whose Lock holds value at path."""
    python = tmp_path / "candidate-python"
    code = STUB.format(answer=CONSISTENT, path=path, value=value)
    python.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(code)} "$@"\n'
    )
    python.chmod(0o755)
    stub = candidate.Candidate(str(python), ARTIFACT)
    with stub.scratch(find_git(tmp_path), tmp_path) as repository:
        with pytest.raises(GatesealError) as raised:
            stub.configure(repository, HYGIENE)
    error = raised.value
    assert (error.code, error.exit_code) == ("candidate_rejected", 2)
    return str(error)


def test_a_candidate_lock_whose_output_digest_differs_is_refused(tmp_path):
    output = ".pre-commit-config.yaml"
    message = disagreement(tmp_path, ["outputs", output, "sha256"], "0" * 64)
    assert all(value in message for value in (__version__, output))


def test_a_candidate_lock_that_pins_another_compiler_is_refused(tmp_path):
    message = disagreement(tmp_path, ["compiler", "sha256"], "d" * 64)
    assert __version__ in message
