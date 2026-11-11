"""Drive a candidate compiler through its public commands.

The candidate is this compiler, started by the suite's Python as a separate
process the way an upgrade starts the one it installs, so its configure and
check run for real. Its artifact names the network locator, which configure
records without downloading.
"""

import os
import stat
import sys
from pathlib import Path

import pytest
from conftest import with_git_config
from gateseal import __version__, candidate
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError
from gateseal.manifest import BundledSource, render
from gateseal.policy import load_bundled_policy
from gateseal.processes import find_git
from test_upgrade_worktree import SOURCE, WHEEL, configure

ARTIFACT = CompilerArtifact("https-wheel", SOURCE, WHEEL, "c" * 64, __version__)
COMPILER = candidate.Candidate(sys.executable, ARTIFACT)
HYGIENE = render(("hygiene",), BundledSource(load_bundled_policy().release))


def test_a_scratch_repository_is_private_and_ignores_git_redirects(
    tmp_path, monkeypatch
):
    """Git configuration reaches the scratch repository; GIT_DIR does not."""
    git = find_git(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    settings = {"init.defaultBranch": "scratch-branch"}
    for key, value in with_git_config(dict(os.environ), settings).items():
        if key.startswith("GIT_CONFIG_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_DIR", str(elsewhere / ".git"))
    with COMPILER.scratch(git, tmp_path) as repository:
        directory = repository.parent
        assert directory.parent == tmp_path
        assert directory.name.startswith(".gateseal-")
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        head = (repository / ".git" / "HEAD").read_text()
        assert head == "ref: refs/heads/scratch-branch\n"
    assert not directory.exists()
    assert not elsewhere.exists()


def test_a_candidate_whose_python_lacks_the_compiler_is_unavailable(tmp_path):
    python = Path(sys.base_prefix) / "bin" / "python3"
    with pytest.raises(GatesealError) as raised:
        candidate.Candidate(str(python), ARTIFACT).run(tmp_path, "configure")
    error = raised.value
    assert (error.code, error.exit_code) == ("candidate_unavailable", 4)
    assert all(value in str(error) for value in (__version__, "configure"))
    assert "No module named gateseal" in str(error)


def test_a_refusing_candidate_names_its_first_diagnostic_and_step(consumer):
    """A failed activation reports the upgrade's targets too; only its cause shows."""
    worktree = configure(consumer, "hygiene")
    worktree.hook.parent.mkdir(exist_ok=True)
    worktree.hook.write_text("#!/bin/sh\nexit 0\n")
    with pytest.raises(GatesealError) as raised:
        COMPILER.run(consumer, "upgrade", "--activate")
    error = raised.value
    assert (error.code, error.exit_code) == ("candidate_rejected", 2)
    named = (__version__, "activation_failed", "unmanaged_hook", str(worktree.hook))
    assert all(value in str(error) for value in named)
    assert "unchanged" not in str(error)
    steps = (str(worktree.hook), "gateseal activate")
    assert all(value in error.next_step for value in steps)


def test_a_candidate_lock_that_records_another_manifest_is_refused(tmp_path):
    """The candidate reads the Manifest as Git stores it, without CR before LF."""
    with COMPILER.scratch(find_git(tmp_path), tmp_path) as repository:
        with pytest.raises(GatesealError) as raised:
            COMPILER.configure(repository, HYGIENE.replace(b"\n", b"\r\n"))
    error = raised.value
    assert error.code == "candidate_rejected"
    assert all(value in str(error) for value in (__version__, "Manifest"))
