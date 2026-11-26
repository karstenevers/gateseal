"""Set up pre-commit and its commit launcher once per machine.

gateseal setup installs the pre-commit version that gateseal names, on the
Python that gateseal names, into a folder that gateseal owns. It writes
pre-commit's commit launcher into a Git template folder there and names that
folder in the global init.templateDir, so Git copies the launcher into every
new clone. It changes no PATH and no tool that the user installed. Undoing it is
unsetting init.templateDir and deleting the tools, bin and git-template folders
under data_root().

The template holds no push launcher: with one there, Git LFS cannot write its
own push hook in a new clone, and a push then uploads no Git LFS object. A
clone gets its push launcher from pre-commit install, which keeps the hook of
Git LFS and runs it first. gateseal setup --refresh also writes UPLOAD_HOOK
beside the push launcher, for Git LFS set up after it, and has Git LFS write
its own hook once the push launcher is gone.
"""

import contextlib
import json
import os
import shlex
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from gateseal import __version__
from gateseal.compiler_release import MINIMUM_UV, PRE_COMMIT
from gateseal.diagnostics import GatesealError, Note, Result
from gateseal.environments import tool_environment, uv_environment
from gateseal.lfs_check import SET_UP
from gateseal.lfs_check import UPLOAD as STARTS_UPLOAD
from gateseal.managed_files import CONFIG, read_optional, write_atomic
from gateseal.processes import MINIMUM_GIT, execute, find_git, find_tool
from gateseal.python_releases import first_trusted, trusted
from gateseal.tools.mise import mise_problem, require_mise
from gateseal.tools.model import LAUNCHERS
from gateseal.validation import version_key
from gateseal.worktree import Worktree, locate_worktree

PYTHON = "3.14"
"""The Python line that setup installs pre-commit on."""

TEMPLATE_SETTING = "init.templateDir"

UPLOAD_HOOK = "hook.gateseal-lfs-upload"
"""The push hook of Git's configuration that starts the upload of Git LFS.

The push launcher takes the push hook file of Git LFS, so Git LFS set up after
the launcher cannot write its hook there. Git starts this hook beside that
file, on every branch and in every worktree of the clone, whatever
configuration each holds.
"""

NO_GIT_LFS = (
    "gateseal: This clone holds Git LFS objects, but git-lfs is not on PATH, so "
    "a push uploads none of them.\nNext: Install Git LFS, then push again."
)

UPLOAD = (
    (
        f"{UPLOAD_HOOK}.command",
        "unset GIT_CONFIG\n"
        f"git config --get-regexp {shlex.quote(SET_UP)} >/dev/null || exit 0\n"
        'test -d "$(git rev-parse --git-common-dir)/lfs" ||'
        " git config lfs.storage >/dev/null || exit 0\n"
        "command -v git-lfs >/dev/null 2>&1 ||"
        f" {{ echo {shlex.quote(NO_GIT_LFS)} >&2; exit 1; }}\n"
        "git lfs pre-push",
    ),
    (f"{UPLOAD_HOOK}.event", "pre-push"),
)
"""The settings of UPLOAD_HOOK, in the order setup writes them.

Git refuses to start any hook while an event names no command, so the command
comes first. Git runs the command in a shell, with the push's arguments, and
adds them to its last line. The upload has a line of its own, which the Git
LFS check counts.

The command does what the hook of Git LFS does, in the clones where Git LFS
writes that hook: Git LFS writes it, and the folder of its objects, when its
filter first runs in a clone. A user's filter of Git LFS applies to every
clone, so the folder, not the filter, tells such a clone. Where git-lfs is
gone, the command stops the push, as that hook does. It reads the settings
that the push reads: GIT_CONFIG makes git config, and no other command, read
only the file it names. Beside a kept hook of Git LFS, Git LFS starts twice,
and the second start finds nothing to upload.
"""

PROBE = (
    "import importlib.metadata, platform; "
    "print(platform.python_version(), importlib.metadata.version('pre-commit'))"
)
"""Print the owned install's Python and pre-commit versions."""


def python_request() -> str:
    """Return the uv request for a trusted release of PYTHON, such as ~=3.14.10.

    A request of the line alone could reuse an older release that uv installed.
    """
    return "~=" + first_trusted(PYTHON)


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
    template folder changes nothing. With clone, that existing clone, which
    Git's template never reaches, also gets the launchers it asks for.
    """
    home = Path.home()
    git = _require(home, "Git", MINIMUM_GIT, find_git)
    uv = _require(home, "uv", MINIMUM_UV, _find_uv)
    notes = _mise_notes()
    worktree = None if clone is None else locate_worktree(clone)
    layout = Layout(data_root())
    configured = _template_configured(git, layout.template)
    _refuse_hooks_path(git, Path("/") if worktree is None else worktree.root)
    changed: list[str] = []
    if not _installed(layout, PRE_COMMIT, home):
        _install(uv, layout, PRE_COMMIT, home)
        changed.append(str(layout.tools / "pre-commit"))
    if _place_launcher(layout, _launcher(layout, home)):
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
        f"pre-commit {PRE_COMMIT} and its commit launcher {state}. "
        "Every new clone runs pre-commit at git commit."
    )
    next_steps = ("Run gateseal setup --refresh in each clone you made before.",)
    if worktree is not None:
        refreshed, kept = _refresh(worktree, layout)
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
    """Refuse a uv that reports a release older than MINIMUM_UV, or none."""
    reported = execute([uv, "--version"], root, tool_environment())
    try:
        found = reported.stdout.decode().split()[1]
        supported = version_key(found) >= version_key(MINIMUM_UV)
    except (IndexError, GatesealError):
        supported = False
    if not supported:
        raise GatesealError(
            "uv_version_mismatch",
            f"gateseal {__version__} requires uv {MINIMUM_UV} or later.",
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
    """Warn about a missing, old or broken mise, which only some Policies need."""
    try:
        require_mise(tool_environment())
    except GatesealError as exc:
        need = (
            "A Policy with a release binary or an npm tool, such as Betterleaks or "
            "Prettier, needs"
        )
        return (Note("warning", exc.code, mise_problem(need, exc)),)
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
    found = git_setting(
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
    folder = git_setting(git, root, "core.hooksPath")
    if folder is not None:
        raise GatesealError(
            "custom_hooks_path",
            f"Git's core.hooksPath names {folder}, so Git would skip the commit "
            "launcher.",
            next_step="Remove the core.hooksPath setting, then run "
            "gateseal setup again.",
        )


def git_setting(git: str, root: Path, *options: str) -> str | None:
    """Return a setting as Git reads it from root, or None when it is unset.

    The setting's name comes last in options. The value decodes as a file name
    does, so bytes that are not UTF-8 survive a comparison with a path. Any
    failure but Git's exit code 1 for an unset setting stops setup.
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
    return os.fsdecode(found.stdout).removesuffix("\n")


def require_pre_commit() -> str:
    """Return the Python of the owned pre-commit, which must start.

    A file that exists is not enough, so the install reports its versions.
    """
    layout = Layout(data_root())
    if not _installed(layout, PRE_COMMIT, Path.home()):
        raise GatesealError(
            "needs_setup",
            f"The pre-commit that gateseal setup installs under {layout.tools} is "
            "missing or does not start.",
            3,
            "gateseal setup",
        )
    return str(layout.python)


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
    return python.startswith(PYTHON + ".") and trusted(python) and version == pre_commit


def _install(uv: str, layout: Layout, pre_commit: str, home: Path) -> None:
    """Install pre-commit with uv into the tool folders that gateseal owns."""
    env = dict(
        uv_environment(),
        UV_TOOL_DIR=str(layout.tools),
        UV_TOOL_BIN_DIR=str(layout.root / "bin"),
    )
    execute(
        [uv, "tool", "install", "--reinstall", "--python", python_request()]
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


def push_launcher(commit: bytes) -> bytes:
    """Return the push launcher that pre-commit writes beside the commit launcher.

    pre-commit writes one script for each hook type, and the scripts differ
    only in the type they name.
    """
    return commit.replace(b"--hook-type=pre-commit", b"--hook-type=pre-push")


def _launchers(folder: Path) -> dict[Path, tuple[bytes, bool]]:
    """Return each launcher in folder with its bytes and whether Git can start it."""
    return {
        path: (path.read_bytes(), os.access(path, os.X_OK))
        for path in (folder / name for name in LAUNCHERS)
        if path.is_file()
    }


def _config_asks_for_push_launcher(root: Path) -> bool:
    """Tell whether the generated configuration in root asks for the push launcher.

    gateseal generates the configuration as JSON. A worktree with another
    configuration, or with none, asks for no push launcher.
    """
    config: object = None
    with contextlib.suppress(OSError, ValueError):
        config = json.loads((root / CONFIG).read_bytes())
    asked = (
        cast("dict[str, object]", config).get("default_install_hook_types")
        if isinstance(config, dict)
        else None
    )
    return isinstance(asked, list) and LAUNCHERS[1] in asked


def asks_for_push_launcher(worktree: Worktree) -> bool:
    """Tell whether a worktree of the clone asks for the push launcher.

    The worktrees of a clone share its launchers, so one that runs a hook at a
    push needs the push launcher in all of them.
    """
    listed = execute(
        [worktree.git, "worktree", "list", "--porcelain", "-z"],
        worktree.root,
        tool_environment(),
    ).stdout
    return any(
        _config_asks_for_push_launcher(
            Path(os.fsdecode(line.removeprefix(b"worktree ")))
        )
        for line in listed.split(b"\0")
        if line.startswith(b"worktree ")
    )


def _refresh(worktree: Worktree, layout: Layout) -> tuple[list[str], tuple[Note, ...]]:
    """Make the launchers of an existing clone the ones its configuration asks for.

    pre-commit install writes the commit launcher, and the push launcher when a
    selected hook runs at a push. It keeps an earlier hook as <launcher>.legacy
    and runs it first. A launcher that is current stays as it is. A push
    launcher of an earlier refresh that no configuration asks for any more runs
    no check and keeps Git LFS from writing its push hook, so pre-commit
    uninstall removes it and puts the kept hook back. UPLOAD_HOOK comes and
    goes with the push launcher, and Git LFS takes the upload over from it
    where no push hook is left.
    """
    pre_commit = [str(layout.python), "-I", "-m", "pre_commit"]
    step = "Fix what pre-commit reports, then run gateseal setup --refresh."
    commit, push = (worktree.hooks / name for name in LAUNCHERS)
    launcher = layout.launcher.read_bytes()
    current = {commit: (launcher, True), push: (push_launcher(launcher), True)}
    asked = asks_for_push_launcher(worktree)
    before = _launchers(worktree.hooks)
    stale = [
        path
        for path in ((commit, push) if asked else (commit,))
        if before.get(path) != current[path]
    ]
    if stale:
        execute(
            [*pre_commit, "install", "--allow-missing-config"]
            + [option for path in stale for option in ("--hook-type", path.name)],
            worktree.root,
            tool_environment(),
            next_step=step,
        )
    if not asked and before.get(push, (None,))[0] == current[push][0]:
        execute(
            [*pre_commit, "uninstall", "--hook-type", push.name],
            worktree.root,
            tool_environment(),
            next_step=step,
        )
    found = _upload_hook_settings(worktree)
    after = _hand_upload_to_git_lfs(worktree, _launchers(worktree.hooks), found)
    changed = [path for path in (commit, push) if before.get(path) != after.get(path)]
    kept = tuple(
        Note(
            "info",
            "legacy_hook",
            f"The launcher {path} runs the earlier hook, which pre-commit keeps "
            f"as {legacy}.",
        )
        for path in changed
        if path in after and (legacy := path.with_name(path.name + ".legacy")).exists()
    )
    names = [str(path) for path in changed]
    left = after.get(push)
    blocks = left is not None and not _starts_upload(left)
    return names + _converge_upload_hook(worktree, found, asked, blocks), kept


def _starts_upload(hook: tuple[bytes, bool]) -> bool:
    """Tell whether Git can start a hook that _launchers read, and it starts Git LFS."""
    return hook[1] and STARTS_UPLOAD.search(hook[0]) is not None


def _hand_upload_to_git_lfs(
    worktree: Worktree,
    launchers: dict[Path, tuple[bytes, bool]],
    found: list[tuple[str, ...]],
) -> dict[Path, tuple[bytes, bool]]:
    """Return the launchers once Git LFS has taken the upload over from UPLOAD_HOOK.

    found is the clone's settings of UPLOAD_HOOK, which stay until Git LFS has
    a push hook, so a refresh that stopped here takes the upload over at its
    next run. Git LFS writes its push hook only when its filter next runs, so
    where UPLOAD would start Git LFS and no push hook is left, git lfs update
    writes it now, and a push before that run uploads too. The push hook, not
    the exit code, tells the result: Git LFS writes that hook first, so a
    conflict on one of its other hooks still hands the upload over.
    """
    push = worktree.hooks / LAUNCHERS[1]
    if push in launchers or not found or not _uses_git_lfs(worktree):
        return launchers
    updated = execute(
        [worktree.git, "lfs", "update"],
        worktree.root,
        tool_environment(),
        allow_failure=True,
    )
    launchers = _launchers(worktree.hooks)
    if push not in launchers:
        output = (updated.stdout + updated.stderr).decode("utf-8", errors="replace")
        raise GatesealError(
            "tool_failed",
            f"git lfs update wrote no push hook at {push}, so {UPLOAD_HOOK} "
            f"stays. Output of Git LFS:\n{output}",
            4,
            "Fix what Git LFS reports, then run gateseal setup --refresh.",
        )
    return launchers


def _uses_git_lfs(worktree: Worktree) -> bool:
    """Tell whether UPLOAD would start Git LFS in the clone.

    Git LFS is set up, the clone holds its objects, and git lfs starts.
    """
    return (
        _succeeds(worktree, "config", "--get-regexp", SET_UP)
        and (
            (worktree.common_dir / "lfs").is_dir()
            or _succeeds(worktree, "config", "--get", "lfs.storage")
        )
        and _succeeds(worktree, "lfs", "version")
    )


def _succeeds(worktree: Worktree, *arguments: str) -> bool:
    """Tell whether a Git command in the clone exits 0."""
    done = execute(
        [worktree.git, *arguments],
        worktree.root,
        tool_environment(),
        allow_failure=True,
    )
    return done.returncode == 0


def _upload_hook_settings(worktree: Worktree) -> list[tuple[str, ...]]:
    """Return the clone's own settings of UPLOAD_HOOK, as key and value."""
    listed = execute(
        [worktree.git, "config", "--local", "--list", "-z"],
        worktree.root,
        tool_environment(),
        next_step="Fix what Git reports, then run gateseal setup --refresh.",
    ).stdout
    return [
        tuple(entry.split("\n", 1))
        for entry in map(os.fsdecode, listed.split(b"\0"))
        if entry.startswith(UPLOAD_HOOK + ".")
    ]


def _converge_upload_hook(
    worktree: Worktree, found: list[tuple[str, ...]], asked: bool, blocks: bool
) -> list[str]:
    """Give the clone exactly the settings of UPLOAD, or none when not asked.

    found is the clone's settings of UPLOAD_HOOK. A push hook file that refresh
    leaves and that uploads nothing, such as a push launcher that pre-commit
    install rewrote or a file that Git cannot start, keeps Git LFS from writing
    its own, so blocks keeps UPLOAD_HOOK that the clone has.
    Return UPLOAD_HOOK when the clone's Git configuration changed.
    """
    git = [worktree.git, "config", "--local"]
    step = "Fix what Git reports, then run gateseal setup --refresh."
    wanted = list(UPLOAD) if asked or (blocks and found) else []
    if found == wanted:
        return []
    if found:
        execute(
            [*git, "--remove-section", UPLOAD_HOOK],
            worktree.root,
            tool_environment(),
            next_step=step,
        )
    for key, value in wanted:
        execute([*git, key, value], worktree.root, tool_environment(), next_step=step)
    return [UPLOAD_HOOK]
