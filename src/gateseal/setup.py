"""Set up pre-commit and its commit launcher once per machine.

gateseal setup installs the pre-commit version that gateseal names, on the
Python that gateseal names, into a folder that gateseal owns. It writes
pre-commit's commit launcher into a Git template folder there and names that
folder in the global init.templateDir, so Git copies the launcher into every
new clone. It changes no PATH and no tool that the user installed. Undoing it is
unsetting init.templateDir and deleting the tools, bin and git-template folders
under data_root().

It installs no push launcher: with one in the template, git lfs install fails
and a push then uploads no Git LFS object.
"""

import os
import shlex
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from gateseal import __version__
from gateseal.bootstrap import MINIMUM_GIT
from gateseal.compiler_release import load_runtime
from gateseal.diagnostics import GatesealError, Note, Result
from gateseal.environments import tool_environment, uv_environment
from gateseal.managed_files import read_optional, write_atomic
from gateseal.processes import execute, find_git, find_tool
from gateseal.tools.release_binary import MINIMUM_MISE, require_mise
from gateseal.validation import version_key
from gateseal.worktree import Worktree, locate_worktree

PYTHON = "3.14"
"""The Python that setup installs pre-commit on: a minor version, so that every
supported uv finds or downloads a patch release of it."""

TEMPLATE_SETTING = "init.templateDir"

PROBE = (
    "import importlib.metadata, platform; "
    "print(platform.python_version(), importlib.metadata.version('pre-commit'))"
)
"""Print the owned install's Python and pre-commit versions."""


def data_root() -> Path:
    """Return the folder that holds everything gateseal setup installs."""
    data = Path(os.environ.get("XDG_DATA_HOME", ""))
    base = data if data.is_absolute() else Path.home() / ".local" / "share"
    return Path(os.path.realpath(base / "gateseal"))


@dataclass(frozen=True)
class Layout:
    """The folders under data_root() and the files setup writes in them."""

    root: Path

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def python(self) -> Path:
        """The Python of the owned pre-commit, which the launcher names."""
        return self.tools / "pre-commit" / "bin" / "python"

    @property
    def template(self) -> Path:
        return self.root / "git-template"

    @property
    def launcher(self) -> Path:
        return self.template / "hooks" / "pre-commit"


def run(clone: Path | None) -> Result:
    """Converge this machine on the owned pre-commit and its launcher.

    Every check runs before the first change, so a missing tool or another
    template folder changes nothing. With clone, the launcher also goes into
    that existing clone, which Git's template never reaches.
    """
    home = Path.home()
    runtime = load_runtime()
    git = _require(home, "Git", MINIMUM_GIT, find_git)
    uv = _require(home, "uv", runtime.minimum_uv, _find_uv)
    notes = _mise_notes()
    worktree = None if clone is None else locate_worktree(clone)
    layout = Layout(data_root())
    configured = _template_configured(git, layout.template)
    _refuse_hooks_path(git, Path("/") if worktree is None else worktree.root)
    pre_commit = runtime.pre_commit
    changed: list[str] = []
    if not _installed(layout, pre_commit, home):
        _install(uv, layout, pre_commit, home)
        changed.append(str(layout.tools / "pre-commit"))
    launcher = _launcher(layout, home)
    if _place_launcher(layout, launcher):
        changed.append(str(layout.launcher))
    if not configured:
        execute(
            [git, "config", "--global", TEMPLATE_SETTING, str(layout.template)],
            home,
            tool_environment(),
        )
        changed.append(TEMPLATE_SETTING)
    state = "are set up" if changed else "were already set up"
    message = (
        f"pre-commit {pre_commit} and its commit launcher {state}. "
        "Every new clone runs pre-commit at git commit."
    )
    next_steps = ("Run gateseal setup --refresh in each clone you made before.",)
    if worktree is not None:
        refreshed, kept = _refresh(worktree, layout, launcher)
        changed += refreshed
        notes += kept
        message += f" So does the clone at {worktree.root}."
        next_steps = ()
    if not changed:
        message += " Nothing changed."
    return Result(
        "setup",
        "set_up" if changed else "already_set_up",
        message,
        changed=tuple(changed),
        next_steps=next_steps,
        notes=notes,
    )


def require_uv(uv: str, root: Path) -> None:
    """Refuse a uv that reports a release older than minimum_uv, or none."""
    reported = execute([uv, "--version"], root, tool_environment())
    minimum = load_runtime().minimum_uv
    try:
        found = reported.stdout.decode().split()[1]
        supported = version_key(found) >= version_key(minimum)
    except (IndexError, GatesealError):
        supported = False
    if not supported:
        raise GatesealError(
            "uv_version_mismatch",
            f"gateseal {__version__} requires uv {minimum} or later.",
            4,
        )


def _find_uv(home: Path) -> str:
    uv = find_tool("uv")
    require_uv(uv, home)
    return uv


def _require(home: Path, name: str, minimum: str, find: Callable[[Path], str]) -> str:
    """Return find's tool, or refuse a missing one by its minimum version."""
    try:
        return find(home)
    except GatesealError as exc:
        if exc.code != "missing_prerequisite":
            raise
        raise GatesealError(
            "missing_prerequisite",
            f"gateseal setup needs {name} {minimum} or later, and none is on PATH.",
            4,
            f"Install {name} {minimum} or later, then run gateseal setup again.",
        ) from exc


def _mise_notes() -> tuple[Note, ...]:
    """Warn about a missing or old mise, which only some Policies need."""
    try:
        require_mise(tool_environment())
    except GatesealError as exc:
        found = "none is" if exc.code == "missing_prerequisite" else "an older one is"
        return (
            Note(
                "warning",
                exc.code,
                f"A Policy with a release binary, such as Betterleaks, needs mise "
                f"{MINIMUM_MISE} or later, and {found} on PATH.",
            ),
        )
    return ()


def _template_configured(git: str, template: Path) -> bool:
    """Return whether init.templateDir names template; refuse another folder."""
    folder = os.environ.get("GIT_TEMPLATE_DIR")
    if folder is not None and not _names(folder, template):
        raise GatesealError(
            "template_conflict",
            f"GIT_TEMPLATE_DIR is set to {shlex.quote(folder)}, so Git ignores "
            f"{TEMPLATE_SETTING} and gateseal setup changed nothing.",
            next_step="Unset GIT_TEMPLATE_DIR where your shell sets it, then run "
            "gateseal setup again.",
        )
    found = _git_setting(
        git, Path("/"), "--show-origin", "-z", "--type=path", TEMPLATE_SETTING
    )
    if found is None:
        return False  # pytestmut: skip[return.none] -- the caller tests its truth
    origin, folder, _ = found.split("\0")
    if not _names(folder, template):
        source = ""
        next_step = (
            f"Remove the {TEMPLATE_SETTING} setting where it is set, then run "
            "gateseal setup again."
        )
        if origin.startswith("file:"):
            path = origin.removeprefix("file:")
            source = f" in {path}"
            next_step = (
                f"Keep what you need from {folder}, run git config --file "
                f"{shlex.quote(path)} --unset {TEMPLATE_SETTING}, then run "
                "gateseal setup again."
            )
        raise GatesealError(
            "template_conflict",
            f"Git's {TEMPLATE_SETTING}{source} already names {folder}, so gateseal "
            "setup changed nothing.",
            next_step=next_step,
        )
    return True


def _names(folder: str, template: Path) -> bool:
    """Return whether folder is an absolute path to template, symlinks resolved."""
    return Path(folder).is_absolute() and Path(os.path.realpath(folder)) == template


def _refuse_hooks_path(git: str, root: Path) -> None:
    """Refuse core.hooksPath as Git sees it from root: Git then skips the launcher.

    root is the filesystem root for new clones and the clone itself for --refresh.
    """
    folder = _git_setting(git, root, "core.hooksPath")
    if folder is not None:
        raise GatesealError(
            "custom_hooks_path",
            f"Git's core.hooksPath names {folder}, so Git would skip the commit "
            "launcher.",
            next_step="Remove the core.hooksPath setting, then run "
            "gateseal setup again.",
        )


def _git_setting(git: str, root: Path, *options: str) -> str | None:
    """Return a setting as Git reads it from root, or None when it is unset.

    The setting's name comes last in options. Any failure but Git's exit code 1
    for an unset setting stops setup.
    """
    found = execute(
        [git, "config", "--includes", "--get", *options],
        root,
        tool_environment(),
        allow_failure=True,
    )
    if found.returncode == 1:
        return None
    if found.returncode != 0:
        detail = (found.stdout + found.stderr).decode("utf-8", errors="replace")
        raise GatesealError(
            "tool_failed",
            detail,
            4,
            "Fix what Git reports, then run gateseal setup again.",
        )
    return found.stdout.decode().removesuffix("\n")


def _installed(layout: Layout, pre_commit: str, home: Path) -> bool:
    """Return whether the owned install runs pre-commit on the named Python."""
    if not layout.python.exists():
        return False  # pytestmut: skip[return.none] -- run tests only its truth
    reported = execute(
        [str(layout.python), "-I", "-c", PROBE],
        home,
        tool_environment(),
        allow_failure=True,
    )
    python, _, version = reported.stdout.decode().strip().partition(" ")
    return python.startswith(PYTHON + ".") and version == pre_commit


def _install(uv: str, layout: Layout, pre_commit: str, home: Path) -> None:
    """Install pre-commit with uv into the tool folders that gateseal owns."""
    env = dict(
        uv_environment(),
        UV_TOOL_DIR=str(layout.tools),
        UV_TOOL_BIN_DIR=str(layout.root / "bin"),
    )
    execute(
        [uv, "tool", "install", "--reinstall", "--python", PYTHON]
        + [f"pre-commit=={pre_commit}"],
        home,
        env,
        next_step="Fix what uv reports, then run gateseal setup again.",
    )


def _launcher(layout: Layout, home: Path) -> bytes:
    """Return the commit launcher that the owned pre-commit writes."""
    with tempfile.TemporaryDirectory() as scratch:
        execute(
            [str(layout.python), "-I", "-m", "pre_commit", "init-templatedir"]
            + ["--hook-type", "pre-commit", scratch],
            home,
            tool_environment(),
        )
        return (Path(scratch) / "hooks" / "pre-commit").read_bytes()


def _place_launcher(layout: Layout, launcher: bytes) -> bool:
    """Write launcher into the template unless it is there and executable."""
    current = read_optional(layout.launcher.parent, layout.launcher.name)
    stale = current != launcher or not os.access(layout.launcher, os.X_OK)
    if stale:
        layout.launcher.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(layout.launcher, launcher, 0o755)
    return stale


def _refresh(
    worktree: Worktree, layout: Layout, launcher: bytes
) -> tuple[list[str], tuple[Note, ...]]:
    """Install the launcher in an existing clone with pre-commit install.

    pre-commit keeps an earlier hook as pre-commit.legacy and runs it first.
    """
    hook = worktree.hook
    try:
        current = hook.read_bytes()
    except FileNotFoundError:
        current = None
    if current == launcher and os.access(hook, os.X_OK):
        return [], ()
    execute(
        [str(layout.python), "-I", "-m", "pre_commit", "install"]
        + ["--hook-type", "pre-commit", "--allow-missing-config"],
        worktree.root,
        tool_environment(),
        next_step="Fix what pre-commit reports, then run gateseal setup --refresh.",
    )
    legacy = hook.with_name("pre-commit.legacy")
    kept = Note(
        "info",
        "legacy_hook",
        f"The commit launcher in {worktree.root} runs the earlier hook, which "
        f"pre-commit keeps as {legacy}.",
    )
    return [str(hook)], (kept,) if legacy.exists() else ()
