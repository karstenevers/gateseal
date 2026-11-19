"""The gateseal-lfs-check hook stops a push that would upload no Git LFS object.

The hook reads a clone's attributes, its commits, its Git LFS setting and its
push hooks, so each test makes a real clone and writes those. Git LFS itself
does not run. test_installed_push pushes with the real Git LFS.
"""

import shutil

import pytest
from conftest import git_path
from gateseal import lfs_check
from gateseal.diagnostics import GatesealError
from installation import success
from stand_ins import executable

GIT = shutil.which("git")
ATTRIBUTES = "*.bin filter=lfs diff=lfs merge=lfs -text\n"
LFS_HOOK = (
    '#!/bin/sh\ncommand -v git-lfs >/dev/null 2>&1 || exit 2\ngit lfs pre-push "$@"\n'
)
LAUNCHER = "#!/bin/sh\nexec pre-commit hook-impl --hook-type=pre-push\n"
POINTER = f"version https://git-lfs.github.com/spec/v1\noid sha256:{'0' * 64}\nsize 5\n"


@pytest.fixture
def clone(tmp_path, monkeypatch):
    """A clone that tracks asset.bin with Git LFS, set up for this user alone."""
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "clone ü"
    success(["git", "init", "--quiet", "--template=", "-b", "main", root], tmp_path)
    # The real filter would write the push hook of Git LFS at git add.
    success(["git", "config", "filter.lfs.clean", "cat"], root)
    success(["git", "config", "user.name", "gateseal test"], root)
    success(["git", "config", "user.email", "gateseal@example.invalid"], root)
    (root / ".gitattributes").write_text(ATTRIBUTES)
    (root / "asset.bin").write_text("pointer\n")
    success(["git", "add", "-A"], root)
    return root


def hook(root, name, text):
    return executable(git_path(root, f"hooks/{name}"), text)


def stopped(root):
    with pytest.raises(GatesealError) as failure:
        lfs_check.check(GIT, root)
    return failure.value


def test_a_push_without_a_git_lfs_hook_stops_and_names_the_repair(clone):
    hook(clone, "pre-push", LAUNCHER)
    error = stopped(clone)
    assert (error.code, str(error), error.next_step) == (
        "lfs_not_uploaded",
        "This repository tracks files with Git LFS, but no push hook of this "
        "clone starts Git LFS, so a push uploads no LFS object.",
        "Run git lfs update --force, then pre-commit install or gateseal setup "
        "--refresh. Then push again.",
    )


def test_a_clone_without_any_push_hook_stops(clone):
    assert stopped(clone).code == "lfs_not_uploaded"


@pytest.mark.parametrize(
    "hooks",
    [
        {"pre-push": LFS_HOOK},
        {"pre-push": LAUNCHER, "pre-push.legacy": LFS_HOOK},
        {"pre-push": "#!/bin/sh\nexec git-lfs pre-push origin\n"},
        {"pre-push": '#!/bin/sh\n\t/opt/bin/git  lfs\tpre-push "$@" || exit 1\n'},
    ],
    ids=["git lfs alone", "kept by pre-commit", "git-lfs", "a path and spaces"],
)
def test_a_push_hook_that_starts_git_lfs_passes(clone, hooks):
    for name, text in hooks.items():
        hook(clone, name, text)
    lfs_check.check(GIT, clone)


@pytest.mark.parametrize("name", ["pre-push", "pre-push.legacy"])
def test_a_git_lfs_hook_that_cannot_start_does_not_count(clone, name):
    """Git skips a hook without execute permission, and so does pre-commit."""
    hook(clone, "pre-push", LAUNCHER)
    hook(clone, name, LFS_HOOK).chmod(0o644)
    assert stopped(clone).code == "lfs_not_uploaded"


@pytest.mark.parametrize(
    "line",
    [
        '  # git lfs pre-push "$@"',
        "exit 0  # git lfs pre-push",
        '#/usr/bin/git lfs pre-push "$@"',
        'echo "Run git lfs pre-push by hand"',
        "git lfs pre-pushes",
        "legit lfs pre-push",
    ],
    ids=[
        "comment",
        "comment after a command",
        "comment with a path",
        "message",
        "longer word",
        "other tool",
    ],
)
def test_a_hook_that_only_names_git_lfs_does_not_count(clone, line):
    hook(clone, "pre-push", f"#!/bin/sh\n{line}\n")
    assert stopped(clone).code == "lfs_not_uploaded"


def test_a_push_hook_of_gits_configuration_that_starts_git_lfs_passes(clone):
    """Git 2.54 and later start it beside the launcher, with the push's updates."""
    hook(clone, "pre-push", LAUNCHER)
    success(["git", "config", "hook.lfs.command", 'git lfs pre-push "$@"'], clone)
    success(["git", "config", "hook.lfs.event", "pre-commit"], clone)
    assert stopped(clone).code == "lfs_not_uploaded"
    success(["git", "config", "hook.lfs.event", "pre-push"], clone)
    lfs_check.check(GIT, clone)
    success(["git", "config", "hook.lfs.enabled", "false"], clone)
    assert stopped(clone).code == "lfs_not_uploaded"


def test_another_push_hook_of_gits_configuration_does_not_count(clone):
    success(["git", "config", "hook.notes.command", "echo lfs"], clone)
    success(["git", "config", "hook.notes.event", "pre-push"], clone)
    assert stopped(clone).code == "lfs_not_uploaded"


def test_another_tools_kept_hook_does_not_count(clone):
    hook(clone, "pre-push", LAUNCHER)
    hook(clone, "pre-push.legacy", "#!/bin/sh\necho lfs\n")
    hook(clone, "pre-push.sample", LFS_HOOK)
    assert stopped(clone).code == "lfs_not_uploaded"


def test_a_user_without_git_lfs_is_not_stopped(clone):
    """Without its filter, a commit holds the file and no pointer to upload."""
    success(["git", "config", "--unset", "filter.lfs.clean"], clone)
    success(["git", "config", "filter.lfs.smudge", "cat"], clone)
    success(["git", "config", "filter.lfsx.process", "cat"], clone)
    hook(clone, "pre-push", LAUNCHER)
    lfs_check.check(GIT, clone)


def test_git_lfs_set_up_with_the_process_filter_alone_counts(clone):
    success(["git", "config", "--unset", "filter.lfs.clean"], clone)
    success(["git", "config", "filter.lfs.process", "git-lfs filter-process"], clone)
    assert stopped(clone).code == "lfs_not_uploaded"


@pytest.mark.parametrize(
    "attributes",
    ["", "*.bin filter=other\n", "*.png filter=lfs\n", "*.bin diff=lfs\n"],
    ids=["none", "another filter", "another file", "another attribute"],
)
def test_a_repository_that_tracks_no_file_with_git_lfs_is_not_stopped(
    clone, attributes
):
    (clone / ".gitattributes").write_text(attributes)
    (clone / "lfs").write_text("a file named as the filter\n")
    success(["git", "add", "-A"], clone)
    hook(clone, "pre-push", LAUNCHER)
    lfs_check.check(GIT, clone)


def test_only_a_file_of_the_index_counts(clone):
    success(["git", "rm", "--quiet", "--cached", "asset.bin"], clone)
    lfs_check.check(GIT, clone)


@pytest.fixture
def deleted(clone):
    """The clone after it committed files and then deleted them, with no file tracked.

    It returns a function that makes both commits from the files' contents by name.
    """
    success(["git", "rm", "--quiet", "--cached", "asset.bin"], clone)
    (clone / ".gitattributes").write_text("")
    hook(clone, "pre-push", LAUNCHER)

    def commit(files):
        for name, content in files.items():
            (clone / name).write_text(content)
        success(["git", "add", "-A"], clone)
        success(["git", "commit", "--quiet", "-m", "Add files"], clone)
        success(["git", "rm", "--quiet", *files], clone)
        success(["git", "commit", "--quiet", "-m", "Delete files"], clone)
        return clone

    return commit


@pytest.mark.parametrize(
    "files",
    [
        {"old.dat": POINTER},
        {"a.txt": "text\n", "b.dat": POINTER},
        {"a.txt": "long\n" * 300, "b.dat": POINTER},
        {"old.dat": POINTER + "x" * (1023 - len(POINTER))},
        {"old.dat": POINTER.replace("oid", "ext-0-zip sha256:abc\noid")},
        {"a\rb.txt": "text\n", "b.dat": POINTER},
    ],
    ids=[
        "alone",
        "after a small file",
        "after a large file",
        "largest",
        "extension",
        "after an unusual name",
    ],
)
def test_a_pointer_in_a_commit_that_the_remote_lacks_counts(deleted, files):
    """The index tracks no file, but a push would send the pointer's commit."""
    assert stopped(deleted(files)).code == "lfs_not_uploaded"


@pytest.mark.parametrize(
    "content",
    [
        "See:\n" + POINTER,
        POINTER + "x" * (1024 - len(POINTER)),
        POINTER.replace("v1\n", "v10\n"),
        POINTER.splitlines(keepends=True)[0] + "A page on the pointer format.\n",
        POINTER.replace("0" * 64, "0" * 63),
    ],
    ids=["quoted", "too large", "another version", "no object", "short object"],
)
def test_a_file_that_is_no_pointer_does_not_count(deleted, content):
    lfs_check.check(GIT, deleted({"old.dat": content}))


def test_a_pointer_in_a_commit_that_the_remote_holds_does_not_count(deleted):
    root = deleted({"old.dat": POINTER})
    success(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], root)
    lfs_check.check(GIT, root, "origin")
    lfs_check.check(GIT, root)
    with pytest.raises(GatesealError):
        lfs_check.check(GIT, root, "fork")


def test_the_hook_reads_the_remote_that_pre_commit_names(deleted, monkeypatch):
    root = deleted({"old.dat": POINTER})
    success(["git", "update-ref", "refs/remotes/origin/main", "HEAD"], root)
    monkeypatch.chdir(root)
    monkeypatch.setenv("PRE_COMMIT_REMOTE_NAME", "fork")
    assert lfs_check.main() == 1
    monkeypatch.setenv("PRE_COMMIT_REMOTE_NAME", "origin")
    assert lfs_check.main() == 0


def test_the_repair_keeps_a_hook_that_pre_commit_kept(clone):
    """git lfs update --force and pre-commit install would replace that hook."""
    hook(clone, "pre-push", LAUNCHER)
    kept = hook(clone, "pre-push.legacy", "#!/bin/sh\necho custom\n")
    assert stopped(clone).next_step == (
        f'Add the line git lfs pre-push "$@" to the hook {kept} and make that '
        "file executable. pre-commit runs it first. Then push again."
    )


def test_a_tracked_file_with_an_unusual_name_counts(clone):
    success(["git", "rm", "--quiet", "--cached", "asset.bin"], clone)
    (clone / "an asset\n ü.bin").write_text("pointer\n")
    success(["git", "add", "-A"], clone)
    assert stopped(clone).code == "lfs_not_uploaded"


def test_a_linked_worktree_reads_the_hooks_of_its_clone(clone, tmp_path):
    success(["git", "commit", "--quiet", "-m", "Track an asset"], clone)
    linked = tmp_path / "linked"
    success(["git", "worktree", "add", "--quiet", linked], clone)
    assert stopped(linked).code == "lfs_not_uploaded"
    hook(clone, "pre-push", LFS_HOOK)
    lfs_check.check(GIT, linked)


def test_the_hook_prints_the_repair_and_fails(clone, monkeypatch, capsys):
    monkeypatch.chdir(clone)
    assert lfs_check.main() == 1
    assert capsys.readouterr() == (
        "",
        "gateseal: This repository tracks files with Git LFS, but no push hook of "
        "this clone starts Git LFS, so a push uploads no LFS object.\n"
        "Next: Run git lfs update --force, then pre-commit install or gateseal "
        "setup --refresh. Then push again.\n",
    )
    hook(clone, "pre-push", LFS_HOOK)
    assert lfs_check.main() == 0
    assert capsys.readouterr() == ("", "")
