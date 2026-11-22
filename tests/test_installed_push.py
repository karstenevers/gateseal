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


def owned_pre_commit():
    """Return the pre-commit that setup installs in the test's home."""
    return Path.home() / ".local/share/gateseal/tools/pre-commit/bin/pre-commit"


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
    success([owned_pre_commit(), "install"], clone)
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
    "Next: Run gateseal setup --refresh, or git lfs update --force and then "
    "pre-commit install. Then push again."
)


def blob(clone):
    """Write a blob that Git LFS tracks, not yet added."""
    (clone / ".gitattributes").write_text("*.bin filter=lfs -text\n")
    (clone / "blob.bin").write_bytes(bytes(range(256)) * 4)


@pytest.fixture
def tracking(pushing, monkeypatch):
    """The clone with a blob that Git LFS tracks, not yet added.

    Git LFS is set up only where a test sets it up: a machine's system
    configuration can hold its filter, so Git does not read that here.
    """
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    blob(pushing.clone)
    return pushing


def add_blob(pushing):
    pushing.git("add", ".gitattributes", "blob.bin")
    return pushing.commit("notes.txt", "ready\n")


def uploaded(pushing):
    """Return the Git LFS objects that the remote holds."""
    return [p for p in (pushing.remote / "lfs").rglob("*") if p.is_file()]


def set_up_git_lfs_late(pushing):
    """Set up Git LFS, which cannot write its push hook beside the launcher."""
    refused = process(["git", "lfs", "install", "--local"], pushing.clone)
    assert refused.returncode != 0, refused.stdout + refused.stderr
    assert "Hook already exists: pre-push" in refused.stdout + refused.stderr


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


def older_configuration(installed, pushing, branch):
    """Commit on a new branch the configuration from before the push hook."""
    pushing.git("switch", "--quiet", "-c", branch)
    manifest = pushing.clone / "gateseal.toml"
    manifest.write_text(manifest.read_text().replace(TABLE, ""))
    installed.cli(pushing.clone, "configure")
    pushing.git("commit", "--quiet", "-a", "-m", "Drop the push hook")


def no_configuration(installed, pushing, branch):
    """Start a branch as if from before the repository adopted gateseal."""
    pushing.git("switch", "--quiet", "--orphan", branch)


@needs_lfs
@pytest.mark.parametrize("start", [older_configuration, no_configuration])
def test_git_lfs_set_up_late_uploads_from_a_branch_without_push_checks(
    installed, tracking, start
):
    """gateseal setup --refresh wrote a push hook of Git's configuration for it."""
    set_up_git_lfs_late(tracking)
    start(installed, tracking, "plain")
    after = add_blob(tracking)
    code, shown = tracking.push("plain")
    assert code == 0, shown
    assert LFS_CHECK not in shown, shown
    assert tracking.pushed("plain") == after
    assert len(uploaded(tracking)) == 1


@needs_lfs
def test_git_lfs_set_up_late_uploads_from_a_linked_worktree_without_push_checks(
    installed, tracking, tmp_path
):
    set_up_git_lfs_late(tracking)
    older_configuration(installed, tracking, "plain")
    tracking.git("switch", "--quiet", "main")
    linked = Pushing(tmp_path / "linked ü", tracking.remote)
    tracking.git("worktree", "add", "--quiet", str(linked.clone), "plain")
    blob(linked.clone)
    after = add_blob(linked)
    code, shown = linked.push("plain")
    assert code == 0, shown
    assert LFS_CHECK not in shown, shown
    assert linked.pushed("plain") == after
    assert len(uploaded(linked)) == 1


@pytest.fixture
def teammate(pushing, tmp_path, monkeypatch):
    """A second clone, set up with a plain pre-commit install, and a blob."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    clone = tmp_path / "teammate"
    success(["git", "clone", "--quiet", pushing.remote, clone], tmp_path)
    second = Pushing(clone, pushing.remote)
    second.git("config", "user.name", "gateseal test")
    second.git("config", "user.email", "gateseal@example.invalid")
    success([owned_pre_commit(), "install"], clone)
    blob(clone)
    return second


@needs_lfs
@pytest.mark.parametrize("repair", ["setup", "pre-commit"])
def test_git_lfs_set_up_late_after_a_plain_pre_commit_install_stops_until_repaired(
    installed, teammate, repair
):
    """pre-commit install writes no upload hook, so the Git LFS check stops the push."""
    before = teammate.pushed()
    set_up_git_lfs_late(teammate)
    after = add_blob(teammate)
    code, shown = teammate.push("main")
    assert code != 0, shown
    assert STOPPED in shown, shown
    assert teammate.pushed() == before
    assert uploaded(teammate) == []
    if repair == "setup":
        installed.cli(teammate.clone, "setup", "--refresh")
    else:
        teammate.git("lfs", "update", "--force")
        success([owned_pre_commit(), "install"], teammate.clone)
    code, shown = teammate.push("main")
    assert code == 0, shown
    assert passed(LFS_CHECK, shown), shown
    assert teammate.pushed() == after
    assert len(uploaded(teammate)) == 1


def test_a_user_without_git_lfs_pushes_a_tracked_file_as_it_is(tracking):
    """Without the filter of Git LFS, a commit holds no pointer to upload."""
    after = add_blob(tracking)
    code, shown = tracking.push("main")
    assert code == 0, shown
    assert passed(LFS_CHECK, shown), shown
    assert tracking.pushed() == after
    assert uploaded(tracking) == []
