import contextlib
import fcntl
import os
import shutil
from collections.abc import Generator
from pathlib import Path

from gateseal import bootstrap
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import open_directory
from gateseal.tools.model import HOOK_STAGES, Stage
from gateseal.worktree import Worktree


def preparation_directory(worktree: Worktree) -> Path:
    """Return the worktree's folder in its Git directory, made when missing.

    Anything else at that path, such as a file or a link, is an unsafe_path.
    """
    local = worktree.preparation
    with contextlib.suppress(FileExistsError):
        local.mkdir(mode=0o700)
    with open_directory(local):
        return local


@contextlib.contextmanager
def operation_lock(local: Path) -> Generator[None]:
    """Hold local's writer lock, and first delete what a killed writer left.

    Every scratch folder in local is named .gateseal-* and is made under this
    lock, so one that is there when the lock is taken has no user. Links are
    never followed.
    """
    descriptor = bootstrap.open_lock(local / "operation.lock")
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


def hook_run(stage: Stage) -> list[str]:
    """Return pre-commit's run arguments for the hooks of one Stage."""
    return ["run", "--config", bootstrap.CONFIG, "--hook-stage", HOOK_STAGES[stage]]
