"""Upgrade to another compiler release and activate with it, in process.

Installing a candidate needs a downloaded wheel, so a stub stands as the
candidate's Python at its process boundary, as in test_candidate_misbehaving.
It stands as release 9.9.9: it passes configure to this compiler with this
release's wheel, then names 9.9.9 as the Lock's compiler. It answers check as
consistent and activate as the candidate's own activation would. Only 9.9.9
can read the Lock the upgrade writes.
"""

import contextlib
import json
import shlex
import sys

from gateseal import __version__, candidate, lock, upgrade
from gateseal.diagnostics import GatesealError
from test_candidate_misbehaving import CONSISTENT
from test_upgrade_worktree import configure

WHEEL = "gateseal-9.9.9-py3-none-any.whl"
ARTIFACTS = f"https://raw.githubusercontent.com/example/wheels/{'0' * 40}/artifacts"
SOURCE = f"{ARTIFACTS}/{WHEEL}"
THIS = f"{ARTIFACTS}/gateseal-{__version__}-py3-none-any.whl"
ACTIVATED = json.dumps(
    {
        "schema": 1,
        "command": "activate",
        "status": "changed",
        "code": "prepared",
        "changed": [".git/hooks/pre-commit"],
        "diagnostics": [{"severity": "info", "category": "prepared", "message": ""}],
        "next_steps": ["Review the hook"],
    }
)
STUB = f"""
import json, subprocess, sys
from pathlib import Path

def pin_compiler(version, source):
    if lock.exists():
        document = json.loads(lock.read_bytes())
        document["compiler"].update(
            version=version,
            filename=source.rsplit("/", 1)[1],
            source={{"kind": "https-wheel", "location": source}},
        )
        lock.write_text(json.dumps(document))

arguments = sys.argv[1:]
command = arguments[arguments.index("--json") + 1]
lock = Path(arguments[arguments.index("--repo") + 1]) / "gateseal.lock"
if command == "check":
    print({CONSISTENT!r})
elif command == "activate":
    print({ACTIVATED!r})
else:
    pin_compiler({__version__!r}, {THIS!r})
    passed = [{THIS!r} if a == {SOURCE!r} else a for a in arguments]
    configured = subprocess.run([sys.executable, *passed]).returncode
    pin_compiler("9.9.9", {SOURCE!r})
    sys.exit(configured)
"""


def test_an_upgrade_to_another_release_activates_with_that_release(
    consumer, tmp_path, monkeypatch
):
    python = tmp_path / "candidate-python"
    python.write_text(
        f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -c {shlex.quote(STUB)} "$@"\n'
    )
    python.chmod(0o755)

    @contextlib.contextmanager
    def installed(artifact, root):
        yield candidate.Candidate(str(python), artifact)

    monkeypatch.setattr(candidate, "installed", installed)
    worktree = configure(consumer, "hygiene")
    asked = upgrade.Request(None, (None, SOURCE, "c" * 64), {}, None, None, True)
    result = upgrade.run(worktree, asked)
    assert (result.code, result.exit_code) == ("upgraded", 0)
    assert "Activated" in result.message
    assert result.changed == ("gateseal.lock", ".git/hooks/pre-commit")
    review, install = result.next_steps[:2]
    assert review == json.loads(ACTIVATED)["next_steps"][0]
    assert all(v in install for v in ("gateseal 9.9.9", "Install a reviewed wheel"))
    assert upgrade.ACTIVATE not in result.next_steps
    written = json.loads((consumer / "gateseal.lock").read_bytes())
    assert written["compiler"]["version"] == "9.9.9"
    try:
        lock.load(consumer, worktree.preparation)
    except GatesealError as refused:
        assert refused.code == "selected_compiler_required"
    else:
        raise AssertionError("this compiler read the Lock of release 9.9.9")
