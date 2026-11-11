import hashlib
import json
import os

import pytest
from conftest import adopt, commit_hygiene, git_path, prepared_state, snapshot
from installation import clone, next_release, package_version, process, success

pytestmark = pytest.mark.integration

CURRENT = package_version()
NEXT = next_release(CURRENT)


def committed_compiler(root):
    lock = success(["git", "show", "HEAD:gateseal.lock"], root).stdout
    return json.loads(lock)["compiler"]["version"]


def doctor_compiler(installation, root):
    """Return the first line of doctor's details, which names the commits' compiler."""
    doctor = installation.cli(root, "doctor")
    assert doctor["code"] == "healthy"
    return doctor["diagnostics"][1]["message"].splitlines()[0]


def configure_next(next_installed, root):
    (root / "gateseal.lock").unlink()
    return next_installed.cli(
        root,
        "configure",
        "--compiler-wheel",
        str(next_installed.wheel),
        "--compiler-sha256",
        next_installed.checksum,
    )


def test_two_clones_keep_their_own_compiler(installed, next_installed, tmp_path):
    first = clone(tmp_path / "first origin", tmp_path / "first clone ü")
    second = clone(tmp_path / "second origin", tmp_path / "second clone ü")
    adopt(installed, first)
    before = snapshot(first)
    adopt(next_installed, second)
    assert snapshot(first) == before
    hooks = [(root / ".git/hooks/pre-commit").read_bytes() for root in (first, second)]
    assert hooks[0] == hooks[1]
    assert installed.cli(first, "status")["code"] == "recorded"
    assert next_installed.cli(second, "status")["code"] == "recorded"
    for installation, root, version in (
        (installed, second, NEXT),
        (next_installed, first, CURRENT),
    ):
        refused = installation.cli(root, "status", code=2)
        assert refused["code"] == "selected_compiler_required"
        message = refused["diagnostics"][0]["message"]
        assert f"Use the locked gateseal {version} compiler." in message
    commit_hygiene(first)
    commit_hygiene(second)
    assert committed_compiler(first) == CURRENT
    assert committed_compiler(second) == NEXT
    assert doctor_compiler(installed, first) == f"gateseal {CURRENT}"
    assert doctor_compiler(next_installed, second) == f"gateseal {NEXT}"


def test_worktrees_of_one_clone_select_different_compilers(
    installed, next_installed, adopted, tmp_path
):
    worktree = tmp_path.resolve() / "next wt ü"
    success(["git", "worktree", "add", "-q", "-b", "next", worktree], adopted)
    before = snapshot(adopted)
    try:
        assert configure_next(next_installed, worktree)["changed"] == ["gateseal.lock"]
        for name in ("gateseal.toml", ".pre-commit-config.yaml"):
            assert (worktree / name).read_bytes() == (adopted / name).read_bytes()
        activated = next_installed.cli(worktree, "activate")
        assert activated["code"] == "prepared"
        assert activated["changed"] == [str(git_path(worktree, "gateseal") / "current")]
        success(["git", "add", "gateseal.lock"], worktree)
        success(["git", "commit", "-m", "Adopt the next compiler"], worktree)
        success(["git", "commit", "--allow-empty", "-m", "Main stays"], adopted)
        assert snapshot(adopted) == before
        assert installed.cli(adopted, "status")["code"] == "recorded"
        assert next_installed.cli(worktree, "status")["code"] == "recorded"
        state = prepared_state(worktree)
        assert state["root"] == str(worktree)
        lock = (worktree / "gateseal.lock").read_bytes()
        assert state["lock_sha256"] == hashlib.sha256(lock).hexdigest()
        assert committed_compiler(worktree) == NEXT
        assert committed_compiler(adopted) == CURRENT
        assert doctor_compiler(next_installed, worktree) == f"gateseal {NEXT}"
    finally:
        success(["git", "worktree", "remove", "--force", worktree], adopted)


def test_staged_lock_selects_the_compiler(installed, next_installed, adopted, tmp_path):
    lock = adopted / "gateseal.lock"
    current = lock.read_bytes()
    configure_next(next_installed, adopted)
    upgraded = lock.read_bytes()
    before = snapshot(adopted)
    (adopted / "ordinary.txt").write_text("ordinary\n")
    success(["git", "add", "ordinary.txt"], adopted)
    success(["git", "commit", "-m", "Ordinary"], adopted)
    assert committed_compiler(adopted) == CURRENT
    assert lock.read_bytes() == upgraded
    assert success(["git", "diff", "--name-only"], adopted).stdout == "gateseal.lock\n"

    success(["git", "add", "gateseal.lock"], adopted)
    staged_lock = success(["git", "rev-parse", ":gateseal.lock"], adopted).stdout
    staged = process(["git", "commit", "-m", "Adopt"], adopted)
    assert staged.returncode != 0
    assert staged.stderr.endswith(
        "gateseal: Staged gateseal.lock differs from the prepared runtime.\n"
        "Next: Run gateseal activate, then retry.\n"
    )
    refused = installed.cli(adopted, "activate", code=2)
    assert refused["code"] == "selected_compiler_required"
    assert f"Use the locked gateseal {NEXT} compiler." in json.dumps(refused)
    assert snapshot(adopted) == before

    lock.write_bytes(current)
    mixed = process(["git", "commit", "-m", "Mixed"], adopted)
    assert mixed.returncode != 0
    assert (
        "gateseal: Staged gateseal.lock differs from the prepared runtime, "
        "and the worktree does not hold the staged gateseal.lock."
    ) in mixed.stderr
    assert "Next: Stage or restore gateseal.lock, then retry." in mixed.stderr
    assert "gateseal activate" not in mixed.stderr

    alternate = dict(os.environ, GIT_INDEX_FILE=str(tmp_path / "alternate index ü"))
    success(["git", "read-tree", "HEAD"], adopted, alternate)
    (adopted / "alternate.txt").write_text("alternate\n")
    success(["git", "add", "alternate.txt"], adopted, alternate)
    success(["git", "commit", "-m", "Alternate"], adopted, alternate)
    real = success(["git", "rev-parse", ":gateseal.lock"], adopted).stdout
    assert real == staged_lock
    cached = success(["git", "diff", "--cached", "--name-status"], adopted).stdout
    assert "D\talternate.txt" in cached.splitlines()

    success(["git", "restore", "--staged", "gateseal.lock"], adopted)
    success(["git", "add", "alternate.txt"], adopted)
    success(["git", "commit", "--allow-empty", "-m", "Back"], adopted)

    lock.write_bytes(upgraded)
    success(["git", "add", "gateseal.lock"], adopted)
    assert next_installed.cli(adopted, "activate")["code"] == "prepared"
    success(["git", "commit", "-m", "Adopt"], adopted)
    assert committed_compiler(adopted) == NEXT
