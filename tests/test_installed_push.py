"""A push hook runs at git push and in the gate, and not at a commit.

A consumer of the bundled policy adds a repository hook with the push and ci
stages. Its script refuses a text file that says DO-NOT-PUSH and records what it
received in the clone's Git directory. Every push is a real git push to a bare
repository, through the launcher that pre-commit wrote.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import on_machine
from installation import process, success

pytestmark = pytest.mark.integration

SCRIPT = """#!/bin/sh
echo "from=$PRE_COMMIT_FROM_REF to=$PRE_COMMIT_TO_REF files=$*" >> .git/slow-check.log
for file in "$@"; do
  if grep -q DO-NOT-PUSH "$file"; then
    echo "$file says DO-NOT-PUSH"
    exit 1
  fi
done
"""
TABLE = (
    "\n[hooks.slow-check]\n"
    'name = "Refuse DO-NOT-PUSH"\n'
    'run = "scripts/slow-check.sh"\n'
    'files = "\\\\.txt$"\n'
    'stages = ["push", "ci"]\n'
)
HOOK = "Refuse DO-NOT-PUSH"
LFS_CHECK = "Check that a push uploads to Git LFS"


@dataclass(frozen=True)
class Pushing:
    """A clone with a push hook, and the bare repository it pushes to."""

    clone: Path
    remote: Path

    @property
    def launcher(self) -> Path:
        return self.clone / ".git" / "hooks" / "pre-push"

    def git(self, *arguments, env=None):
        return success(["git", *arguments], self.clone, env).stdout.strip()

    def commit(self, name, text, env=None):
        """Commit one file and return the new head."""
        (self.clone / name).write_text(text)
        self.git("add", name, env=env)
        self.git("commit", "--quiet", "-m", f"Add {name}", env=env)
        return self.git("rev-parse", "HEAD")

    def push(self, *refspec, env=None):
        pushed = process(["git", "push", "origin", *refspec], self.clone, env)
        return pushed.returncode, pushed.stdout + pushed.stderr

    def pushed(self, branch="main"):
        """Return the commit the remote has for branch, or None."""
        found = process(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            self.remote,
        )
        return found.stdout.strip() or None

    def calls(self):
        """Return what each run of the hook's script recorded, oldest first."""
        log = self.clone / ".git" / "slow-check.log"
        return log.read_text().splitlines() if log.exists() else []


def passed(name, shown):
    return f"{name}{'.' * (79 - len(name) - len('Passed'))}Passed" in shown


@pytest.fixture(scope="module")
def module_pushing(installed, tmp_path_factory):
    """Adopt the push hook, install both launchers and push the adoption."""
    root = tmp_path_factory.mktemp("pushing consumer ü").resolve()
    remote = root / "remote.git"
    with on_machine(installed):
        success(["git", "init", "--quiet", "--bare", "-b", "main", remote], root)
        success(["git", "clone", "--quiet", remote, root / "clone"], root)
        pushing = Pushing(root / "clone", remote)
        pushing.git("config", "user.name", "gateseal test")
        pushing.git("config", "user.email", "gateseal@example.invalid")
        script = pushing.clone / "scripts/slow-check.sh"
        script.parent.mkdir()
        script.write_text(SCRIPT)
        script.chmod(0o755)
        (pushing.clone / "first.txt").write_text("ready\n")
        installed.cli(pushing.clone, "configure", "--capability", "hygiene")
        manifest = pushing.clone / "gateseal.toml"
        manifest.write_text(manifest.read_text() + TABLE)
        installed.cli(pushing.clone, "configure")
        installed.cli(pushing.clone, "setup", "--refresh")
        pushing.git("add", "-A")
        pushing.git("commit", "--quiet", "-m", "Add a push hook")
        code, shown = pushing.push("main")
        assert code == 0, shown
    return pushing


@pytest.fixture
def pushing(module_pushing, tmp_path):
    """A copy of the adopted clone and its remote, whose hook has not run."""
    copy = Pushing(tmp_path / "clone ü", tmp_path / "remote.git")
    shutil.copytree(module_pushing.remote, copy.remote)
    shutil.copytree(module_pushing.clone, copy.clone, symlinks=True)
    copy.git("remote", "set-url", "origin", str(copy.remote))
    (copy.clone / ".git" / "slow-check.log").unlink()
    return copy


def test_the_first_push_runs_the_hook_on_every_file(module_pushing):
    """A branch that is new to the remote has no range, so every file is checked."""
    assert module_pushing.calls() == ["from= to= files=first.txt"]


def test_a_new_clone_gets_the_push_launcher_from_pre_commit_install(
    installed, module_pushing, tmp_path
):
    """Git's template gives a clone the commit launcher alone."""
    clone = tmp_path / "second clone"
    success(["git", "clone", "--quiet", module_pushing.remote, clone], tmp_path)
    hooks = clone / ".git" / "hooks"
    assert [path.name for path in hooks.iterdir()] == ["pre-commit"]
    owned = Path.home() / ".local/share/gateseal/tools/pre-commit/bin/pre-commit"
    success([owned, "install"], clone)
    assert sorted(path.name for path in hooks.iterdir()) == ["pre-commit", "pre-push"]
    assert b"--hook-type=pre-push" in (hooks / "pre-push").read_bytes()
    second = Pushing(clone, module_pushing.remote)
    second.git("config", "user.name", "gateseal test")
    second.git("config", "user.email", "gateseal@example.invalid")
    second.commit("notes.txt", "DO-NOT-PUSH\n")
    code, shown = second.push("HEAD:refs/heads/second")
    assert code != 0 and "notes.txt says DO-NOT-PUSH" in shown, shown
    assert second.pushed("second") is None


def test_a_push_hook_runs_in_the_gate_and_not_at_a_commit_or_in_run(installed, pushing):
    (pushing.clone / "notes.txt").write_text("DO-NOT-PUSH\n")
    pushing.git("add", "notes.txt")
    committed = process(["git", "commit", "-m", "Add notes"], pushing.clone)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == 0, shown
    assert HOOK not in shown and LFS_CHECK not in shown, shown
    run = installed.cli(pushing.clone, "run")
    assert run["code"] == "checks_passed"
    assert all(d.get("hook") != "slow-check" for d in run["diagnostics"])
    assert pushing.calls() == []
    gate = installed.cli(pushing.clone, "gate", code=1)
    assert gate["code"] == "checks_failed"
    [entry] = [d for d in gate["diagnostics"] if d.get("hook") == "slow-check"]
    assert (entry["origin"], entry["tool"], entry["status"]) == (
        "repository",
        "scripts/slow-check.sh",
        "failed",
    )
    assert all(d.get("hook") != "gateseal-lfs-check" for d in gate["diagnostics"])


def test_a_push_gives_the_hook_the_changed_files_and_the_pushed_range(pushing):
    before = pushing.pushed()
    pushing.commit("notes.txt", "ready\n")
    after = pushing.commit("more notes.txt", "ready\n")
    code, shown = pushing.push("main")
    assert code == 0, shown
    assert passed(HOOK, shown) and passed(LFS_CHECK, shown), shown
    assert pushing.calls() == [
        f"from={before} to={after} files=more notes.txt notes.txt"
    ]
    assert pushing.pushed() == after


@pytest.mark.parametrize("branch", ["main", "topic"], ids=["existing", "new"])
def test_a_failing_push_hook_stops_the_push_before_the_remote_changes(pushing, branch):
    before = pushing.pushed()
    pushing.git("switch", "--quiet", "-C", branch)
    pushing.commit("notes.txt", "DO-NOT-PUSH\n")
    code, shown = pushing.push(branch)
    assert code != 0, shown
    assert "notes.txt says DO-NOT-PUSH" in shown, shown
    assert pushing.pushed("main") == before
    assert pushing.pushed(branch) == (before if branch == "main" else None)
    after = pushing.commit("notes.txt", "ready\n")
    code, shown = pushing.push(branch)
    assert code == 0, shown
    assert pushing.pushed(branch) == after
    first, second = pushing.calls()
    assert first.endswith(" files=notes.txt") and second.endswith(" files=notes.txt")
    start = second.removeprefix("from=").split(" to=")[0]
    assert pushing.git("rev-parse", start) == before
    assert f" to={after} " in second


def test_a_ranged_gate_gives_the_hook_the_range_a_push_gives(installed, pushing):
    before = pushing.pushed()
    after = pushing.commit("notes.txt", "ready\n")
    gate = installed.cli(pushing.clone, "gate", "--base", before, "--head", after)
    assert gate["code"] == "checks_passed"
    assert pushing.calls() == [f"from={before} to={after} files=notes.txt"]


def test_status_and_doctor_check_the_push_launcher(installed, pushing):
    def check(command, code=0):
        result = installed.cli(pushing.clone, command, code=code)
        [entry] = [
            d for d in result["diagnostics"] if d.get("check") == "push-launcher"
        ]
        return result, entry["status"], entry["message"]

    for command in ("status", "doctor"):
        result, status, message = check(command)
        assert (result["code"], status) == ("healthy", "passed")
        assert message == f"{pushing.launcher} is the push launcher."
    pushing.launcher.unlink()
    result, status, message = check("doctor", 3)
    assert (result["code"], status) == ("unhealthy", "failed")
    assert message == f"{pushing.launcher} is missing, so git push runs no hook."
    assert result["next_steps"] == ["gateseal setup --refresh"]
    refreshed = installed.cli(pushing.clone, "setup", "--refresh")
    assert refreshed["changed"] == [str(pushing.launcher)]
    assert installed.cli(pushing.clone, "setup", "--refresh")["changed"] == []
    assert check("doctor")[1] == "passed"


needs_lfs = pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs Git LFS")
STOPPED = (
    "gateseal: This repository tracks files with Git LFS, but no push hook of this "
    "clone starts Git LFS, so a push uploads no LFS object.\n"
    "Next: Run git lfs update --force, then pre-commit install or gateseal setup "
    "--refresh. Then push again."
)


@pytest.fixture
def tracking(pushing, monkeypatch):
    """The clone with a blob that Git LFS tracks, not yet added.

    Git LFS is set up only where a test sets it up: a machine's system
    configuration can hold its filter, so Git does not read that here.
    """
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (pushing.clone / ".gitattributes").write_text("*.bin filter=lfs -text\n")
    (pushing.clone / "blob.bin").write_bytes(bytes(range(256)) * 4)
    return pushing


def add_blob(pushing):
    pushing.git("add", ".gitattributes", "blob.bin")
    return pushing.commit("notes.txt", "ready\n")


def uploaded(pushing):
    """Return the Git LFS objects that the remote holds."""
    return [p for p in (pushing.remote / "lfs").rglob("*") if p.is_file()]


@needs_lfs
def test_git_lfs_set_up_before_the_push_launcher_uploads_at_a_push(installed, tracking):
    """pre-commit keeps the hook of Git LFS and runs it before its own hooks."""
    tracking.launcher.unlink()
    tracking.git("lfs", "install", "--local")
    refreshed = installed.cli(tracking.clone, "setup", "--refresh")
    assert refreshed["changed"] == [str(tracking.launcher)]
    assert [d["category"] for d in refreshed["diagnostics"][1:]] == ["legacy_hook"]
    after = add_blob(tracking)
    code, shown = tracking.push("main")
    assert code == 0, shown
    assert passed(HOOK, shown) and passed(LFS_CHECK, shown), shown
    assert tracking.pushed() == after
    assert len(uploaded(tracking)) == 1


@needs_lfs
def test_git_lfs_set_up_after_the_push_launcher_stops_the_push_until_repaired(
    installed, tracking
):
    """Git LFS cannot write its hook then, and the push would upload nothing."""
    before = tracking.pushed()
    refused = process(["git", "lfs", "install", "--local"], tracking.clone)
    assert refused.returncode != 0, refused.stdout + refused.stderr
    after = add_blob(tracking)
    code, shown = tracking.push("main")
    assert code != 0, shown
    assert STOPPED in shown, shown
    assert tracking.pushed() == before
    assert uploaded(tracking) == []
    tracking.git("lfs", "update", "--force")
    installed.cli(tracking.clone, "setup", "--refresh")
    code, shown = tracking.push("main")
    assert code == 0, shown
    assert passed(LFS_CHECK, shown), shown
    assert tracking.pushed() == after
    assert len(uploaded(tracking)) == 1


def test_a_user_without_git_lfs_pushes_a_tracked_file_as_it_is(tracking):
    """Without the filter of Git LFS, a commit holds no pointer to upload."""
    after = add_blob(tracking)
    code, shown = tracking.push("main")
    assert code == 0, shown
    assert passed(LFS_CHECK, shown), shown
    assert tracking.pushed() == after
    assert uploaded(tracking) == []
