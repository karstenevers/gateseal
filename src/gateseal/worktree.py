"""Find the worktree a command runs in, and where Git keeps its state."""

import contextlib
import fcntl
import os
import shutil
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.environments import tool_environment
from gateseal.managed_files import open_directory
from gateseal.paths import resolved
from gateseal.processes import execute, find_git

PREPARATION = "gateseal"
"""A worktree's folder for gateseal's writer lock and stored policy releases.

It is in the worktree's own Git directory. Git treats this name as
per-worktree, as `git rev-parse --git-path gateseal` shows.
"""


@dataclass(frozen=True)
class Worktree:
    """Where Git keeps one worktree's files, its own state and its shared state.

    root is the worktree's top. git_dir is its own Git directory, which holds its
    index and its preparation: <root>/.git in a clone, <common>/worktrees/<id> in
    a linked worktree. common_dir holds what every worktree shares, the hooks
    among them. git is the absolute git that located them. Every path is
    resolved, as open_directory requires. Only locate_worktree builds one.
    """

    root: Path
    git_dir: Path
    common_dir: Path
    git: str

    @property
    def preparation(self) -> Path:
        return self.git_dir / PREPARATION

    @property
    def hooks(self) -> Path:
        """The folder of the hooks that Git starts, launchers among them."""
        return self.common_dir / "hooks"


def locate_worktree(requested: Path) -> Worktree:
    """Ask Git which worktree contains requested and where its paths are.

    One rev-parse names the top, the Git directory, the common directory and the
    index, so a clone, a linked worktree of a clone or of a bare repository and
    a clone with a separate Git directory resolve alike. A bare repository has
    no top, so Git's refusal is not_a_repository.

    Commands read the worktree's files and its own index. A GIT_INDEX_FILE that
    names another index is refused rather than ignored, because run would check
    that index's files and cleanup would build pre-images from it. Commits are
    where alternate indexes are checked.
    """
    env = tool_environment()
    step = "Run gateseal inside a Git worktree, or pass --repo with its path."
    if not requested.is_dir():
        raise GatesealError(
            "not_a_repository", f"{requested} is not a directory.", next_step=step
        )
    git = find_git(requested)
    response = execute(
        [git, "-C", str(requested), "rev-parse", "--path-format=absolute"]
        + ["--show-toplevel", "--absolute-git-dir", "--git-common-dir"]
        + ["--git-path", "index"],
        requested,
        env,
        allow_failure=True,
    )
    if response.returncode:
        detail = response.stderr.decode(errors="replace").strip()
        raise GatesealError(
            "not_a_repository",
            f"Git cannot use {requested} as a worktree: {detail}",
            next_step=step,
        )
    lines = response.stdout.decode().split("\n")
    if len(lines) != 5 or lines[4]:
        raise GatesealError("unsupported_path", "A Git path contains a line break.")
    root, git_dir, common_dir, index = (resolved(line) for line in lines[:4])
    if index != git_dir / "index":
        raise GatesealError(
            "alternate_index",
            f"GIT_INDEX_FILE makes Git use {index}, not this worktree's index "
            f"{git_dir / 'index'}. Commits check the index Git names; gateseal "
            "commands read the worktree and its own index.",
            next_step="Unset GIT_INDEX_FILE, then retry.",
        )
    return Worktree(root, git_dir, common_dir, git)


def preparation_directory(worktree: Worktree) -> Path:
    """Return the worktree's folder in its Git directory, made when missing.

    Anything else at that path, such as a file or a link, is an unsafe_path.
    """
    local = worktree.preparation
    with contextlib.suppress(FileExistsError):
        local.mkdir(mode=0o700)
    with open_directory(local):
        return local


def open_lock(name: Path) -> int:
    """Open lock file name read-write without following a link, creating it.

    On macOS an O_CREAT open can fail with ENOENT while another process
    creates the same file, so a missing lock is created with O_EXCL and opened
    again when another process wins.
    """
    flags = os.O_RDWR | os.O_NOFOLLOW
    while True:
        try:
            return os.open(name, flags)
        except FileNotFoundError:
            pass
        try:
            return os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass


@contextlib.contextmanager
def operation_lock(local: Path) -> Generator[None]:
    """Hold local's writer lock, and first delete what a killed writer left.

    Every scratch folder in local is named .gateseal-* and is made under this
    lock, so one that is there when the lock is taken has no user. Links are
    never followed.
    """
    descriptor = open_lock(local / "operation.lock")
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GatesealError(
                "operation_in_progress", "Another gateseal writer is active.", 4
            ) from exc
        with open_directory(local) as directory:
            for name in os.listdir(directory):
                if name.startswith(".gateseal-"):
                    shutil.rmtree(name, dir_fd=directory, ignore_errors=True)
        yield
    finally:
        os.close(descriptor)
