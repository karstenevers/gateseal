"""Activation refuses a CPython build that links out of the interpreter store.

uv's CPython install needs the network, so a uv first on PATH answers
python install with a tree that holds a link to /, and passes every other
command to the real uv. Sealing the entry refuses the link before anything
runs from it, and activate and upgrade --activate both report the refusal as
needs_activation.
"""

import contextlib
import io
import json
import os
import shutil

import pytest
from conftest import TRACKED
from gateseal import bootstrap, interpreter_store, upgrade
from gateseal.cli import main
from prepared_attempts import executable
from test_upgrade_worktree import REASON, REVIEW_BY, configure, request

ESCAPE = "symlink"


@pytest.fixture
def escaping_uv(tmp_path, monkeypatch):
    uv = tmp_path / "escaping uv" / "uv"
    executable(
        uv,
        '#!/bin/sh\nif [ "$1 $2" = "python install" ]; then\n'
        '  while [ "$1" != --install-dir ]; do shift; done\n'
        '  mkdir -p "$2/bin" && ln -s / "$2/bin/escape"\n'
        f'  exit 0\nfi\nexec "{shutil.which("uv")}" "$@"\n',
    )
    monkeypatch.setenv("PATH", os.pathsep.join([str(uv.parent), os.environ["PATH"]]))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv cache"))
    monkeypatch.setenv(interpreter_store.STORE_VARIABLE, str(tmp_path / "store"))


def test_activate_reports_the_escaping_link_as_needs_activation(consumer, escaping_uv):
    worktree = configure(consumer, "hygiene")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(consumer), "--json", "activate"])
    result = json.loads(output.getvalue())
    assert (code, result["code"], result["next_steps"]) == (
        3,
        "needs_activation",
        ["gateseal activate"],
    )
    assert ESCAPE in result["diagnostics"][0]["message"]
    assert not list(worktree.preparation.glob("attempt-*"))


def test_an_upgrade_keeps_its_files_when_the_build_escapes(consumer, escaping_uv):
    worktree = configure(consumer, "hygiene")
    older = request(
        components={"pre-commit-hooks": "5.0.0"},
        flags=(REASON, REVIEW_BY),
        activate=True,
    )
    result = upgrade.run(worktree, older)
    assert (result.code, result.exit_code) == ("activation_failed", 3)
    assert all(value in result.message for value in ("needs_activation", ESCAPE))
    activate, review = result.next_steps
    assert activate == bootstrap.ACTIVATE
    assert "git diff" in review
    assert sorted(result.changed) == sorted(TRACKED)
    assert 'version = "5.0.0"' in (consumer / "gateseal.toml").read_text()
