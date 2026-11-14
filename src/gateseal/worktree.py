"""Find the worktree a command runs in, and where Git keeps its state."""

from dataclasses import dataclass
from pathlib import Path

from gateseal import bootstrap
from gateseal.diagnostics import GatesealError
from gateseal.environments import tool_environment
from gateseal.processes import execute, find_git


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
        return self.git_dir / bootstrap.PREPARATION

    @property
    def hook(self) -> Path:
        return self.common_dir / "hooks" / "pre-commit"

    def display(self, path: Path) -> str:
        """Name path relative to root when it is inside, otherwise absolutely."""
        return (
            path.relative_to(self.root).as_posix()
            if path.is_relative_to(self.root)
            else str(path)
        )


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
    root, git_dir, common_dir, index = (Path(line).resolve() for line in lines[:4])
    if index != git_dir / "index":
        raise GatesealError(
            "alternate_index",
            f"GIT_INDEX_FILE makes Git use {index}, not this worktree's index "
            f"{git_dir / 'index'}. Commits check the index Git names; gateseal "
            "commands read the worktree and its own index.",
            next_step="Unset GIT_INDEX_FILE, then retry.",
        )
    return Worktree(root, git_dir, common_dir, git)
