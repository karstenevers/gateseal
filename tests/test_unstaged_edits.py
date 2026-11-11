import contextlib
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import published
from installation import process, success

pytestmark = pytest.mark.integration

STAGED = b"staged line\n"
UNSTAGED = b"unstaged edit one\nunstaged edit two\n"

PAUSE = """#!/bin/sh
[ -n "${{_PRE_COMMIT_SKIP_POST_CHECKOUT-}}" ] || exit 0
[ -e {paused} ] && exit 0
ps -o ppid= -p "$PPID" | tr -d ' ' > {paused}.tmp
mv {paused}.tmp {paused}
waited=0
while [ ! -e {release} ] && [ "$waited" -lt 1200 ]; do
  sleep 0.1
  waited=$((waited + 1))
done
"""


@dataclass
class PausedCommit:
    process: subprocess.Popen[bytes]
    pre_commit: int
    release: Path
    output: Path

    def resume(self):
        self.release.touch()
        self.process.wait(timeout=180)
        return self.process.returncode, self.output.read_text()


def stage_with_unstaged_edit(root, unstaged=UNSTAGED):
    (root / "staged.txt").write_bytes(STAGED)
    success(["git", "add", "staged.txt"], root)
    (root / "notes.txt").write_bytes(unstaged)


def reprepare(installed, root):
    (root / ".git/hooks/pre-commit").unlink()
    result = installed.cli(root, "activate")
    assert result["code"] == "prepared", result
    return result


def apply_steps(result):
    return [step for step in result["next_steps"] if "git apply" in step]


@contextlib.contextmanager
def paused_commit(root, tmp_path):
    paused, release = tmp_path / "paused", tmp_path / "release"
    hook = root / ".git/hooks/post-checkout"
    hook.write_text(
        PAUSE.format(paused=shlex.quote(str(paused)), release=shlex.quote(str(release)))
    )
    hook.chmod(0o755)
    output = tmp_path / "commit output"
    with output.open("wb") as log:
        commit = subprocess.Popen(
            ["git", "commit", "-m", "Paused"],
            cwd=root,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 120
        while not paused.exists():
            assert commit.poll() is None, (
                "the commit ended before pre-commit stashed the unstaged edit: "
                + output.read_text()
            )
            assert time.monotonic() < deadline, "pre-commit never stashed the edit"
            time.sleep(0.05)
        yield PausedCommit(commit, int(paused.read_text()), release, output)
    finally:
        release.touch()
        commit.wait(timeout=180)


def test_commit_survives_two_activations(installed, adopted, tmp_path):
    stage_with_unstaged_edit(adopted)
    with paused_commit(adopted, tmp_path) as commit:
        pinned = published(adopted)
        first = reprepare(installed, adopted)
        second = reprepare(installed, adopted)
        survived = pinned.is_dir()
        code, output = commit.resume()
    assert code == 0, f"the commit failed after two activations:\n{output}"
    assert (adopted / "notes.txt").read_bytes() == UNSTAGED, (
        "the unstaged edit is not back in the worktree"
    )
    committed = success(["git", "show", "HEAD:staged.txt"], adopted).stdout
    assert committed.encode() == STAGED, "the staged change was not committed"
    assert survived, f"activation deleted {pinned.name} while a commit ran from it"
    assert apply_steps(first) + apply_steps(second) == [], (
        "activation told the user to apply a patch the commit still owned"
    )


@pytest.mark.parametrize(
    ("unstaged", "mode"),
    [(UNSTAGED, 0o644), (b"original\n", 0o755)],
    ids=["content", "executable bit"],
)
def test_restored_patch_is_not_reported(installed, adopted, unstaged, mode):
    stage_with_unstaged_edit(adopted, unstaged)
    (adopted / "notes.txt").chmod(mode)
    success(["git", "commit", "-m", "Ordinary"], adopted)
    notes = adopted / "notes.txt"
    assert (notes.read_bytes(), notes.stat().st_mode & 0o777) == (unstaged, mode), (
        "pre-commit did not restore the unstaged edit"
    )
    pinned = published(adopted)
    (patch,) = (pinned / "pre-commit-home").glob("patch*")
    yesterday = time.time() - 86400
    os.utime(patch, (yesterday, yesterday))
    first = reprepare(installed, adopted)
    second = reprepare(installed, adopted)
    assert apply_steps(first) + apply_steps(second) == [], (
        "activation told the user to apply edits the commit already restored"
    )
    patches = adopted / ".git/gateseal/patches"
    kept = sorted(patches.iterdir()) if patches.exists() else []
    assert kept == [], "activation kept a patch whose edits are already restored"
    assert not pinned.exists(), "activation kept an attempt whose patch is restored"


def test_patch_outlives_an_orphaned_checkout(installed, adopted):
    (adopted / "notes.txt").write_bytes(b"head\ntail\n")
    success(["git", "add", "notes.txt"], adopted)
    success(["git", "commit", "--allow-empty", "-m", "Notes"], adopted)
    stage_with_unstaged_edit(adopted, b"head\n")
    tree = success(["git", "write-tree"], adopted).stdout.strip()
    diff = subprocess.run(
        [
            "git",
            "diff-index",
            "--ignore-submodules",
            "--binary",
            "--exit-code",
            "--no-color",
            "--no-ext-diff",
            tree,
            "--",
        ],
        cwd=adopted,
        capture_output=True,
    )
    assert diff.returncode == 1, diff.stderr
    home = published(adopted) / "pre-commit-home"
    (home / f"patch{int(time.time())}-1").write_bytes(diff.stdout)
    # As when pre-commit was killed while the git checkout child it started
    # rewrote notes.txt and had written only the edit's head so far.
    time.sleep(0.1)
    (adopted / "notes.txt").write_bytes(b"head\n")
    steps = apply_steps(reprepare(installed, adopted)) + apply_steps(
        reprepare(installed, adopted)
    )
    success(["git", "checkout", "--", "notes.txt"], adopted)
    steps += apply_steps(reprepare(installed, adopted))
    patches = adopted / ".git/gateseal/patches"
    kept = sorted(patches.iterdir()) if patches.exists() else []
    assert len(kept) == 1, "activation deleted a patch the checkout then needed"
    recovery = f"git apply --whitespace=nowarn {kept[0]}"
    assert [step for step in steps if recovery in step], (
        f"no activation told the user to {recovery}: {steps}"
    )
    success(["git", "apply", "--whitespace=nowarn", kept[0]], adopted)
    assert (adopted / "notes.txt").read_bytes() == b"head\n", (
        "applying the reported patch did not restore the unstaged edit"
    )


@pytest.mark.parametrize(
    ("committed", "unstaged", "whitespace"),
    [
        (b"original\n", UNSTAGED, "warn"),
        (b"same\n" * 10, b"same\n" * 11, "warn"),
        (b"original\n", b"original  \n", "fix"),
    ],
    ids=["distinct lines", "repeated lines", "trailing space with whitespace fix"],
)
def test_killed_commit_patch_is_reported(
    installed, adopted, tmp_path, committed, unstaged, whitespace
):
    success(["git", "config", "apply.whitespace", whitespace], adopted)
    (adopted / "notes.txt").write_bytes(committed)
    success(["git", "add", "notes.txt"], adopted)
    success(["git", "commit", "--allow-empty", "-m", "Notes"], adopted)
    stage_with_unstaged_edit(adopted, unstaged)
    with paused_commit(adopted, tmp_path) as commit:
        command = process(
            ["ps", "-ww", "-o", "command=", "-p", commit.pre_commit], adopted
        )
        assert "pre_commit" in command.stdout, command.stdout
        os.kill(commit.pre_commit, signal.SIGKILL)
        assert commit.process.wait(timeout=60) != 0, commit.output.read_text()
        commit.resume()
    assert (adopted / "notes.txt").read_bytes() == committed, (
        "the hard kill left the unstaged edit in the worktree"
    )
    steps = apply_steps(reprepare(installed, adopted)) + apply_steps(
        reprepare(installed, adopted)
    )
    (patch,) = (adopted / ".git/gateseal/patches").iterdir()
    recovery = f"git apply --whitespace=nowarn {patch}"
    assert [step for step in steps if recovery in step], (
        f"no activation told the user to {recovery}: {steps}"
    )
    success(["git", "apply", "--whitespace=nowarn", patch], adopted)
    assert (adopted / "notes.txt").read_bytes() == unstaged, (
        "applying the reported patch did not restore the unstaged edit"
    )
