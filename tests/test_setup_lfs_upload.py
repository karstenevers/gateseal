"""gateseal setup --refresh gives a clone with the push launcher a Git LFS upload.

The push launcher takes the push hook file of Git LFS. A push hook of Git's
configuration starts Git LFS beside it, on every branch and in every worktree.
A stand-in git-lfs records each start, so these tests run without Git LFS.
test_installed_push uploads with the real Git LFS.
"""

import os
import shlex
import shutil

import pytest
from gateseal import setup
from installation import process, success
from test_setup import (
    commit,
    configuration,
    gateseal,
    linked_worktree,
    machine,
    notes,
    repository,
)

__all__ = ["machine"]

HOOK = "hook.gateseal-lfs-upload"

WRITES_ITS_PUSH_HOOK = (
    "printf '#!/bin/sh\\ngit lfs pre-push \"$@\"\\n' > .git/hooks/pre-push\n"
    "chmod +x .git/hooks/pre-push\n"
)
"""A stand-in git-lfs that writes its push hook, as git lfs update does."""


def stand_in(machine, script):
    """Put a git-lfs first on PATH that runs script, except for git lfs version,
    which exits 0 as it does where Git LFS starts."""
    machine.shim("git-lfs", 'if [ "$1" = version ]; then exit 0; fi\n' + script)


def settings(clone):
    """Return the clone's own settings of the upload hook, as Git lists them."""
    listed = process(
        ["git", "config", "--local", "-z", "--get-regexp", r"^hook\."], clone
    )
    return [tuple(entry.split("\n", 1)) for entry in listed.stdout.split("\0") if entry]


@pytest.fixture
def pushing(machine, tmp_path):
    """A clone whose configuration asks for the push launcher, and its remote."""
    gateseal("setup")
    remote = tmp_path / "remote.git"
    success(["git", "init", "--quiet", "--bare", "--template=", remote], tmp_path)
    clone = repository(tmp_path / "clone")
    (clone / ".pre-commit-config.yaml").write_text(
        configuration(tmp_path / "pre-commit.log", "pre-commit", "pre-push")
    )
    success(["git", "remote", "add", "origin", remote], clone)
    return clone


@pytest.fixture
def lfs(machine, tmp_path, monkeypatch):
    """Return the log of a stand-in git-lfs: its arguments, then its input.

    Its git lfs version logs nothing, and its git lfs update writes its push
    hook. Neither reads input.

    Git LFS is set up only where a test sets it up: a machine's system
    configuration can hold its filter, so Git does not read that here.
    """
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    log = tmp_path / "git-lfs.log"
    stand_in(
        machine,
        f'echo "$*" >> {shlex.quote(str(log))}\n'
        'if [ "$1" = update ]; then\n'
        f"{WRITES_ITS_PUSH_HOOK}"
        "else\n"
        f"  cat >> {shlex.quote(str(log))}\n"
        "fi\n",
    )
    return log


def test_refresh_writes_the_upload_hook_with_the_push_launcher(pushing):
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    hooks = pushing / ".git" / "hooks"
    assert (code, result["code"]) == (0, "set_up")
    assert result["changed"] == [str(hooks / "pre-push"), HOOK]
    assert settings(pushing) == [
        (f"{HOOK}.command", setup.UPLOAD[0][1]),
        (f"{HOOK}.event", "pre-push"),
    ]
    listed = success(["git", "hook", "list", "pre-push"], pushing).stdout
    assert listed.splitlines() == ["gateseal-lfs-upload", "hook from hookdir"]
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    assert (code, result["code"], result["changed"]) == (0, "already_set_up", [])


def test_a_clone_without_the_push_launcher_gets_no_upload_hook(machine, tmp_path):
    gateseal("setup")
    clone = repository(tmp_path / "clone")
    (clone / ".pre-commit-config.yaml").write_text(configuration(tmp_path / "log"))
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["changed"]) == (0, [])
    assert settings(clone) == []


def push(clone, *refspec):
    pushed = process(["git", "push", "--quiet", "origin", *refspec], clone)
    assert pushed.returncode == 0, pushed.stdout + pushed.stderr


def set_up_git_lfs(clone, *setting):
    """Set a filter of Git LFS, and make the folder where it keeps its objects."""
    setting = setting or ("filter.lfs.process", "git-lfs filter-process")
    success(["git", "config", *setting], clone)
    (clone / ".git" / "lfs").mkdir()


@pytest.mark.parametrize(
    "filter",
    [
        ("filter.lfs.process", "git-lfs filter-process"),
        ("filter.lfs.clean", "git-lfs clean -- %f"),
    ],
    ids=["process", "clean"],
)
def test_the_upload_hook_starts_git_lfs_with_the_push(pushing, lfs, filter):
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing, *filter)
    assert commit(pushing).returncode == 0
    head = success(["git", "rev-parse", "HEAD"], pushing).stdout.strip()
    push(pushing, "main")
    remote = pushing.parent / "remote.git"
    assert lfs.read_text() == (
        f"pre-push origin {remote}\nrefs/heads/main {head} refs/heads/main {'0' * 40}\n"
    )
    assert (pushing.parent / "pre-commit.log").read_text() == "pre-commit\n"


def test_the_upload_hook_starts_git_lfs_where_lfs_storage_keeps_its_objects(
    pushing, lfs, tmp_path
):
    """lfs.storage moves the objects of Git LFS out of the clone's Git folder."""
    gateseal("--repo", pushing, "setup", "--refresh")
    success(["git", "config", "filter.lfs.process", "git-lfs filter-process"], pushing)
    success(["git", "config", "lfs.storage", str(tmp_path / "lfs-objects")], pushing)
    assert commit(pushing).returncode == 0
    push(pushing, "main")
    assert lfs.read_text().startswith("pre-push origin ")


@pytest.mark.parametrize("filter", [False, True], ids=["no filter", "no objects"])
def test_the_upload_hook_does_not_start_git_lfs_where_it_is_not_set_up(
    pushing, lfs, filter
):
    """A user's filter applies to every clone, but a clone without objects of
    Git LFS has none to upload."""
    gateseal("--repo", pushing, "setup", "--refresh")
    if filter:
        success(
            ["git", "config", "filter.lfs.process", "git-lfs filter-process"], pushing
        )
    assert commit(pushing).returncode == 0
    push(pushing, "main")
    assert not lfs.exists()
    assert (pushing.parent / "pre-commit.log").read_text() == "pre-commit\n"


def test_the_upload_hook_reads_the_settings_that_the_push_reads(
    pushing, lfs, tmp_path, monkeypatch
):
    """GIT_CONFIG makes git config, and no other command, read only that file."""
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing)
    assert commit(pushing).returncode == 0
    (tmp_path / "other.gitconfig").write_text("")
    monkeypatch.setenv("GIT_CONFIG", str(tmp_path / "other.gitconfig"))
    push(pushing, "main")
    assert lfs.read_text().startswith("pre-push origin ")


@pytest.fixture
def without_git_lfs(machine, tmp_path, monkeypatch):
    """A clone with the upload hook and a filter of Git LFS, but no git-lfs.

    The clone has no push launcher, and PATH holds only git.
    """
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    remote = tmp_path / "remote.git"
    success(["git", "init", "--quiet", "--bare", "--template=", remote], tmp_path)
    clone = repository(tmp_path / "clone", "--template=")
    success(["git", "remote", "add", "origin", remote], clone)
    success(["git", "config", "filter.lfs.clean", "cat"], clone)
    for key, value in setup.UPLOAD:
        success(["git", "config", key, value], clone)
    monkeypatch.setenv("PATH", str(machine.shims))
    assert commit(clone).returncode == 0
    return clone


def test_the_upload_hook_passes_a_clone_without_git_lfs_objects_where_git_lfs_is_gone(
    without_git_lfs,
):
    push(without_git_lfs, "main")


def test_the_upload_hook_stops_a_clone_with_git_lfs_objects_where_git_lfs_is_gone(
    without_git_lfs,
):
    (without_git_lfs / ".git" / "lfs").mkdir()
    pushed = process(["git", "push", "--quiet", "origin", "main"], without_git_lfs)
    assert pushed.returncode != 0
    assert setup.NO_GIT_LFS in pushed.stderr
    remote = without_git_lfs.parent / "remote.git"
    assert process(["git", "rev-parse", "--verify", "main"], remote).returncode != 0


def test_the_upload_hook_runs_on_a_branch_and_in_a_worktree_without_push_hooks(
    pushing, lfs, tmp_path
):
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing)
    linked = linked_worktree(pushing, tmp_path / "linked ü")
    success(["git", "switch", "--quiet", "-c", "plain"], linked)
    push(linked, "plain")
    (pushing / ".pre-commit-config.yaml").unlink()
    success(["git", "switch", "--quiet", "--orphan", "other"], pushing)
    success(["git", "commit", "--quiet", "--allow-empty", "-m", "Other"], pushing)
    push(pushing, "other")
    started = [line for line in lfs.read_text().splitlines() if "pre-push" in line]
    assert len(started) == 2
    assert not (tmp_path / "pre-commit.log").exists()


@pytest.mark.parametrize(
    "change",
    [
        [f"{HOOK}.command", "true"],
        ["--unset", f"{HOOK}.event"],
        ["--add", f"{HOOK}.event", "post-commit"],
        ["--unset", f"{HOOK}.command"],
    ],
    ids=["command", "no event", "another event", "no command"],
)
def test_refresh_restores_a_changed_upload_hook(pushing, change):
    gateseal("--repo", pushing, "setup", "--refresh")
    written = settings(pushing)
    success(["git", "config", "--local", *change], pushing)
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    assert (code, result["code"], result["changed"]) == (0, "set_up", [HOOK])
    assert settings(pushing) == written


def test_refresh_removes_the_upload_hook_with_the_push_launcher(pushing):
    """Git LFS can then write its own push hook, which uploads alone."""
    gateseal("--repo", pushing, "setup", "--refresh")
    (pushing / ".pre-commit-config.yaml").unlink()
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    launcher = pushing / ".git" / "hooks" / "pre-push"
    assert (code, result["changed"]) == (0, [str(launcher), HOOK])
    assert settings(pushing) == []
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    assert (code, result["code"], result["changed"]) == (0, "already_set_up", [])


def retire(clone):
    """Drop the push hook from the configuration, and refresh."""
    (clone / ".pre-commit-config.yaml").unlink()
    return gateseal("--repo", clone, "setup", "--refresh")


@pytest.mark.parametrize("storage", [False, True], ids=["objects", "lfs.storage"])
def test_refresh_has_git_lfs_write_its_hooks_when_it_removes_the_push_launcher(
    pushing, lfs, tmp_path, storage
):
    """Git LFS writes them itself only at its next filter run, after which a
    push could already have come."""
    gateseal("--repo", pushing, "setup", "--refresh")
    if storage:
        success(
            ["git", "config", "filter.lfs.process", "git-lfs filter-process"], pushing
        )
        success(
            ["git", "config", "lfs.storage", str(tmp_path / "lfs-objects")], pushing
        )
    else:
        set_up_git_lfs(pushing)
    code, result = retire(pushing)
    launcher = pushing / ".git" / "hooks" / "pre-push"
    assert (code, result["changed"], notes(result)) == (0, [str(launcher), HOOK], [])
    assert lfs.read_text() == "update\n"


@pytest.mark.parametrize("filter", [False, True], ids=["no filter", "no objects"])
def test_refresh_leaves_git_lfs_alone_where_it_is_not_set_up(pushing, lfs, filter):
    gateseal("--repo", pushing, "setup", "--refresh")
    if filter:
        success(
            ["git", "config", "filter.lfs.process", "git-lfs filter-process"], pushing
        )
    else:
        (pushing / ".git" / "lfs").mkdir()
    code, result = retire(pushing)
    launcher = pushing / ".git" / "hooks" / "pre-push"
    assert (code, result["changed"]) == (0, [str(launcher), HOOK])
    assert not lfs.exists()


def exec_path(tmp_path, monkeypatch):
    """Give Git an exec path of its own, so the host's git-lfs is not in it."""
    folder = tmp_path / "git-core"
    folder.mkdir()
    monkeypatch.setenv("GIT_EXEC_PATH", str(folder))
    return folder


def test_refresh_removes_the_push_launcher_as_before_where_git_lfs_is_gone(
    pushing, machine, monkeypatch, tmp_path
):
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing)
    monkeypatch.setenv("PATH", str(machine.shims))
    exec_path(tmp_path, monkeypatch)
    code, result = retire(pushing)
    launcher = pushing / ".git" / "hooks" / "pre-push"
    assert (code, result["changed"], notes(result)) == (0, [str(launcher), HOOK], [])
    assert not launcher.exists()


def test_refresh_finds_git_lfs_where_only_git_finds_it(
    pushing, machine, monkeypatch, tmp_path
):
    """Git looks for git-lfs in its exec path before PATH, as it does when the
    upload hook runs git lfs pre-push."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing)
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "chmod").symlink_to(shutil.which("chmod"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(machine.shims), str(tools)]))
    git_lfs = exec_path(tmp_path, monkeypatch) / "git-lfs"
    git_lfs.write_text(
        '#!/bin/sh\nif [ "$1" = version ]; then exit 0; fi\n' + WRITES_ITS_PUSH_HOOK
    )
    git_lfs.chmod(0o755)
    code, result = retire(pushing)
    launcher = pushing / ".git" / "hooks" / "pre-push"
    assert (code, result["changed"]) == (0, [str(launcher), HOOK])
    assert launcher.read_text() == '#!/bin/sh\ngit lfs pre-push "$@"\n'
    assert os.access(launcher, os.X_OK)


@pytest.mark.parametrize("status", [2, 0])
def test_refresh_stops_where_git_lfs_writes_no_push_hook(
    pushing, machine, monkeypatch, status
):
    """Git LFS can also exit 0 and write its hooks where Git does not look."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    stand_in(
        machine,
        f'echo "open .git/hooks/pre-push: permission denied"; exit {status}\n',
    )
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing)
    code, result = retire(pushing)
    assert (code, result["code"]) == (4, "tool_failed")
    message = result["diagnostics"][0]["message"]
    assert str(pushing / ".git" / "hooks" / "pre-push") in message
    assert message.endswith("\nopen .git/hooks/pre-push: permission denied\n")
    assert result["next_steps"] == [
        "Fix what Git LFS reports, then run gateseal setup --refresh."
    ]
    assert settings(pushing) == list(setup.UPLOAD)
    stand_in(machine, WRITES_ITS_PUSH_HOOK)
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    launcher = pushing / ".git" / "hooks" / "pre-push"
    assert (code, result["changed"]) == (0, [str(launcher), HOOK])
    assert settings(pushing) == []


def test_refresh_leaves_git_lfs_alone_in_a_clone_without_the_upload_hook(
    machine, tmp_path, lfs
):
    """Git LFS set up in a clone that never had the push launcher writes its
    own hooks when its filter runs."""
    gateseal("setup")
    clone = repository(tmp_path / "clone")
    (clone / ".pre-commit-config.yaml").write_text(configuration(tmp_path / "log"))
    set_up_git_lfs(clone)
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["changed"]) == (0, [])
    assert not lfs.exists()


def test_refresh_keeps_the_upload_hook_where_git_lfs_writes_a_hook_git_cannot_start(
    pushing, machine, monkeypatch
):
    """A umask without the owner's execute bit leaves the hook of Git LFS
    without it, and Git skips that hook."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    stand_in(machine, WRITES_ITS_PUSH_HOOK + "chmod -x .git/hooks/pre-push\n")
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing)
    code, result = retire(pushing)
    assert (code, result["changed"]) == (0, [str(pushing / ".git/hooks/pre-push")])
    assert settings(pushing) == list(setup.UPLOAD)


def test_refresh_hands_the_upload_over_where_only_another_git_lfs_hook_conflicts(
    pushing, machine, monkeypatch
):
    """Git LFS writes its push hook first, then stops at the user's own
    post-checkout hook."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    stand_in(
        machine,
        WRITES_ITS_PUSH_HOOK
        + 'echo "Hook already exists: post-checkout" >&2; exit 2\n',
    )
    gateseal("--repo", pushing, "setup", "--refresh")
    set_up_git_lfs(pushing)
    code, result = retire(pushing)
    launcher = pushing / ".git" / "hooks" / "pre-push"
    assert (code, result["changed"]) == (0, [str(launcher), HOOK])
    assert launcher.read_text() == '#!/bin/sh\ngit lfs pre-push "$@"\n'
    assert settings(pushing) == []


def test_refresh_keeps_the_upload_hook_while_a_push_hook_file_keeps_git_lfs_out(
    pushing,
):
    """A launcher that pre-commit install rewrote stays, and Git LFS cannot write
    its hook beside it."""
    gateseal("--repo", pushing, "setup", "--refresh")
    written = settings(pushing)
    launcher = pushing / ".git" / "hooks" / "pre-push"
    launcher.write_bytes(launcher.read_bytes() + b"# another pre-commit\n")
    (pushing / ".pre-commit-config.yaml").unlink()
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    assert (code, result["changed"]) == (0, [])
    assert settings(pushing) == written
    launcher.write_text('#!/bin/sh\ngit lfs pre-push "$@"\n')
    code, result = gateseal("--repo", pushing, "setup", "--refresh")
    assert (code, result["changed"]) == (0, [HOOK])
    assert settings(pushing) == []


def test_refresh_keeps_a_push_hook_of_gits_configuration_that_it_did_not_write(
    pushing,
):
    success(["git", "config", "hook.mine.event", "pre-push"], pushing)
    success(["git", "config", "hook.mine.command", "true"], pushing)
    gateseal("--repo", pushing, "setup", "--refresh")
    (pushing / ".pre-commit-config.yaml").unlink()
    gateseal("--repo", pushing, "setup", "--refresh")
    assert settings(pushing) == [
        ("hook.mine.event", "pre-push"),
        ("hook.mine.command", "true"),
    ]
