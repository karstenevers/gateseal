"""The gateseal-lfs-check hook: keep a push from skipping its Git LFS upload.

Git LFS uploads a push's objects from its own pre-push hook, and pre-commit's
push launcher takes that hook's place. pre-commit install keeps a hook it
finds as pre-push.legacy and runs it first, so Git LFS keeps uploading when it
was set up before the launcher. Set up later, Git LFS cannot write its hook,
and a push then passes without an upload. This hook stops that push when a
file of the index, or a commit that the remote lacks, is of Git LFS.
pre-commit builds it from gateseal's repository at the commit the Lock pins and
runs it at every push of a selection with a push hook.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.processes import find_tool

UPLOAD = re.compile(
    rb"^[ \t]*(?:exec[ \t]+)?(?:[^\s#]\S*/)?git(?:[ \t]+|-)lfs[ \t]+pre-push\b",
    re.MULTILINE,
)
"""A line of a hook that uploads: it starts git lfs pre-push, or git-lfs pre-push.

A comment or a message that names the command starts nothing, so it does not
count. Neither does a command behind another one on its line: the check then
stops the push, which is the safe side, and names the line to add.
"""

POINTER = re.compile(
    rb"version https://git-lfs\.github\.com/spec/v1\n"
    rb"(?:ext-[^\n]*\n)*oid sha256:[0-9a-f]{64}\n"
)
"""How the file starts that Git LFS commits in place of a tracked file."""

POINTER_LIMIT = 1024
"""Git LFS reads no file of this many bytes or more as a pointer."""

REMOTE = "PRE_COMMIT_REMOTE_NAME"
"""Where pre-commit names the remote of the push."""


def _git(git: str, root: Path, *arguments: str, stdin: bytes = b"") -> bytes:
    return subprocess.run(
        [git, *arguments], cwd=root, input=stdin, capture_output=True, check=True
    ).stdout


def _optional(git: str, root: Path, *arguments: str) -> bytes:
    """Return what Git prints, or nothing when Git fails, as for a missing setting."""
    return subprocess.run(
        [git, *arguments], cwd=root, capture_output=True, check=False
    ).stdout


def tracks(git: str, root: Path) -> bool:
    """Tell whether an attribute gives a file of the index the lfs filter."""
    shown = _git(
        git,
        root,
        *("check-attr", "-z", "--stdin", "filter"),
        stdin=_git(git, root, "ls-files", "-z"),
    )
    return b"lfs" in shown.split(b"\0")[2::3]


def remote_lacks_pointer(git: str, root: Path, remote: str | None) -> bool:
    """Tell whether a commit that remote lacks holds a Git LFS pointer.

    remote is the name of a remote, or None for every remote. Git knows what a
    remote holds from the clone's remote-tracking branches. pre-commit names
    one branch of a push only, so every commit of the clone counts.
    """
    held = "--remotes" if remote is None else f"--remotes={remote}"
    listed = _git(
        git,
        root,
        *("rev-list", "--objects", "--no-object-names", "--all"),
        "--not",
        held,
    )
    small = b"".join(
        name + b"\n"
        for name, kind, size in map(
            bytes.split,
            _git(git, root, "cat-file", "--batch-check", stdin=listed).splitlines(),
        )
        if kind == b"blob" and int(size) < POINTER_LIMIT
    )
    files = _git(git, root, "cat-file", "--batch", stdin=small)
    # Each object is a line of its name, kind and size, then the file and a
    # newline. A start one byte late only cuts a name, which nothing reads.
    start = 0  # pytestmut: skip[num.shift] -- see above
    while start < len(files):
        end = files.index(b"\n", start)
        if POINTER.match(files, end + 1):
            return True
        size = int(files[start:end].split()[2])
        start = end + size + 2  # pytestmut: skip[num.shift] -- see above
    return False  # pytestmut: skip[return.none] -- check tests only its truth


def lfs_configured(git: str, root: Path) -> bool:
    """Tell whether Git LFS is set up for this user, so commits hold its pointers.

    Git makes a pointer with the clean filter or with the process filter.
    """
    setting = r"^filter\.lfs\.(clean|process)$"
    return bool(_optional(git, root, "config", "--get-regexp", setting))


def uploads(git: str, root: Path, hooks: Path) -> bool:
    """Tell whether a push hook of this clone starts Git LFS.

    Git starts the push hook in hooks, and pre-commit the one it kept. Git 2.54
    and later also start each push hook of Git's configuration, which
    git hook list names. An older Git lists none.
    """
    names = _optional(git, root, "hook", "list", "-z", "pre-push").split(b"\0")
    return any(
        os.access(hook, os.X_OK) and UPLOAD.search(hook.read_bytes())
        for hook in (hooks / "pre-push", hooks / "pre-push.legacy")
    ) or any(
        UPLOAD.search(_optional(git, root, "config", "--get", f"hook.{name}.command"))
        for name in map(os.fsdecode, names)
    )


def check(git: str, root: Path, remote: str | None = None) -> None:
    """Fail when this clone commits Git LFS pointers that no push hook uploads.

    A user who has not set up Git LFS is not stopped. For that user no hook
    uploads without the push launcher either, and no repair works without
    Git LFS. remote names the remote of the push, as remote_lacks_pointer reads it.
    """
    if not lfs_configured(git, root):
        return
    found = _git(
        git, root, "rev-parse", "--path-format=absolute", "--git-path", "hooks"
    )
    hooks = Path(os.fsdecode(found.removesuffix(b"\n")))
    if uploads(git, root, hooks):
        return
    if not (tracks(git, root) or remote_lacks_pointer(git, root, remote)):
        return
    kept = hooks / "pre-push.legacy"
    raise GatesealError(
        "lfs_not_uploaded",
        "This repository tracks files with Git LFS, but no push hook of this "
        "clone starts Git LFS, so a push uploads no LFS object.",
        # git lfs update --force and pre-commit install would replace a kept hook.
        next_step=f'Add the line git lfs pre-push "$@" to the hook {kept} and make '
        "that file executable. pre-commit runs it first. Then push again."
        if kept.exists()
        else "Run git lfs update --force, then pre-commit install or gateseal "
        "setup --refresh. Then push again.",
    )


def main() -> int:
    root = Path.cwd()
    try:
        check(find_tool("git"), root, os.environ.get(REMOTE))
    except GatesealError as exc:
        print(f"gateseal: {exc}\nNext: {exc.next_step}", file=sys.stderr)
        return 1
    return 0
