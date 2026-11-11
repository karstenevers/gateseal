import contextlib
import fcntl
import os
import platform
import shutil
import tempfile
import time
import uuid
from collections.abc import Generator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from gateseal import (
    __version__,
    bootstrap,
    configured_hooks,
    hook_store,
    interpreter_store,
    lock,
)
from gateseal.bootstrap import sha
from gateseal.compiler_release import TARGETS, load_runtime, verify_wheel
from gateseal.diagnostics import GatesealError, Result
from gateseal.downloads import compiler_download
from gateseal.environments import (
    package_environment,
    pre_commit_environment,
    tool_environment,
    uv_environment,
)
from gateseal.managed_files import open_directory, read_optional, write_atomic
from gateseal.processes import execute, find_git, find_tool, pre_commit_command
from gateseal.tools import pre_commit_repository, release_binary
from gateseal.tools.model import HOOK_STAGES, Stage, Tool
from gateseal.validation import canonical, version_key


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


def preparation_directory(worktree: Worktree, create: bool = False) -> Path:
    local = worktree.preparation
    if create:
        local.mkdir(mode=0o700, exist_ok=True)
    if local.exists() or local.is_symlink():
        with open_directory(local):
            return local
    return local


@contextlib.contextmanager
def operation_lock(local: Path) -> Generator[None]:
    descriptor = bootstrap.open_lock(local / "operation.lock")
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GatesealError(
                "operation_in_progress", "Another gateseal writer is active.", 4
            ) from exc
        yield
    finally:
        os.close(descriptor)


UNSUPPORTED_PATH = "=\"'$`\\" + os.pathsep


def require_supported_path(worktree: Worktree) -> None:
    """Refuse a Git directory that the hook's env, PATH or pip's launchers misread.

    Attempts live in the worktree's Git directory, so the hook passes that path
    to env and pip writes it into launchers. env reads an argument with = as a
    variable. pip's launcher for a script whose interpreter path has a space is
    both a shell script and a Python string, so quotes, $, backticks and
    backslashes in that path break it. The hooks' PATH starts with the
    attempt's tools directory, so a colon would split that entry in two. A
    linked worktree's Git directory is named after the folder git worktree add
    created.
    """
    found = sorted(set(str(worktree.git_dir)) & set(UNSUPPORTED_PATH))
    if found:
        refused = " ".join(UNSUPPORTED_PATH)
        in_clone = set(str(worktree.common_dir)) & set(UNSUPPORTED_PATH)
        raise GatesealError(
            "unsupported_path",
            f"gateseal prepares in {worktree.git_dir}, whose path contains "
            f"{' '.join(found)}, which gateseal cannot prepare.",
            next_step=f"Move the clone to a path without {refused}, "
            "then run gateseal activate."
            if in_clone
            else "Remove this worktree with git worktree remove, add it again at "
            f"a folder whose name has none of {refused}, then run gateseal "
            "activate there.",
        )


def require_supported_store() -> None:
    """Refuse an interpreter store that pip's script launchers misread.

    Hook environments live in the store, so pip writes the store's path into
    their launchers as it writes the Git directory's into the attempt's.
    virtualenv, which builds them, refuses a path with a colon.
    """
    store = interpreter_store.location().resolve()
    found = sorted(set(str(store)) & set(UNSUPPORTED_PATH))
    if found:
        raise GatesealError(
            "unsupported_path",
            f"The interpreter store {store} has {' '.join(found)} in its path, "
            "where gateseal cannot prepare hook environments.",
            next_step=f"Set {interpreter_store.STORE_VARIABLE} to an absolute path "
            f"without {' '.join(UNSUPPORTED_PATH)}, then run gateseal activate.",
        )


def require_owned_hook(worktree: Worktree) -> None:
    configured = execute(
        [worktree.git, "config", "--show-origin", "--get", "core.hooksPath"],
        worktree.root,
        tool_environment(),
        allow_failure=True,
    )
    if configured.returncode == 0:
        raise GatesealError(
            "custom_hooks_path",
            "Custom core.hooksPath is unsupported. gateseal does not manage "
            "checks in that hooks path; commits can run without gateseal checks.",
            next_step=(
                f"Change your working directory to {worktree.root}. "
                "Review git config --show-origin --show-scope --get-all "
                "core.hooksPath. Remove each setting from its reported file with "
                "git config --file <path> --unset-all core.hooksPath, or remove "
                "the command/environment override for command scope. Repeat "
                "until git config --get core.hooksPath finds no value, "
                "then run gateseal activate."
            ),
        )
    if configured.returncode != 1:
        raise GatesealError(
            "git_configuration_failed", "Could not inspect core.hooksPath.", 4
        )
    hook_path = worktree.hook
    hook = read_optional(hook_path.parent, hook_path.name)
    if hook is not None and hook != bootstrap.HOOK:
        raise GatesealError(
            "unmanaged_hook",
            f"Existing pre-commit hook {hook_path} is not the gateseal hook.",
            next_step=(
                f"Preserve {hook_path} outside {hook_path.parent}, "
                f"move it out of {hook_path.parent}, then run gateseal activate. "
                "Review the preserved hook before reusing it."
            ),
        )
    configured_hooks.require_no_pre_commit_hook(
        worktree.git, worktree.root, tool_environment()
    )


PATCHES = "patches"
PATCH_GRACE_SECONDS = 3600


def hook_run(stage: Stage) -> list[str]:
    """Return pre-commit's run arguments for the hooks of one Stage."""
    return ["run", "--config", bootstrap.CONFIG, "--hook-stage", HOOK_STAGES[stage]]


@dataclass(frozen=True)
class Plan:
    """The inputs _plan verified and selected before any attempt or download."""

    locked: lock.Locked
    git: Path
    uv: str
    environment: dict[str, str]
    python: interpreter_store.Archive
    hook_python: tuple[str, interpreter_store.Archive] | None
    compiler_source: str
    mise: release_binary.Mise | None


def activate(worktree: Worktree, locked: lock.Locked, override: str | None) -> Result:
    """Converge the worktree on a healthy, published preparation of the Lock.

    Hook ownership, the wheel, the platform and uv are checked before an attempt
    is created or anything is downloaded; _publish checks the tracked policy and
    hook ownership again once the attempt is built. Until _publish renames
    current, it names the attempt it named before, or nothing, and cleanup in
    this run or the next removes what an interrupted run left. Only this
    worktree's preparation, the shared hook when it is missing, and the
    machine-wide interpreter store are written. An interpreter entry that fails
    its seal is reinstalled with the same digests, so no other worktree's or
    clone's preparation changes. A hook entry is rebuilt with new digests, so
    other worktrees' and clones' commits stop until they activate again.
    """
    root, git = worktree.root, worktree.git
    require_supported_path(worktree)
    require_supported_store()
    local = preparation_directory(worktree, create=True)
    worktree.hook.parent.mkdir(exist_ok=True)
    require_owned_hook(worktree)
    wheel = verify_wheel(Path(override), locked.artifact) if override else None
    _clean_up(local, root, git)
    try:
        if _healthy(worktree, locked):
            code, message, changed = (
                "already_prepared",
                "Preparation and hooks are healthy.",
                (),
            )
        else:
            attempt = _prepare(_plan(locked, Path(git), wheel), local)
            changed = _publish(worktree, locked, attempt)
            code = "prepared"
            message = (
                "Exact compiler, Python, pre-commit, and selected checks are prepared."
            )
    finally:
        _clean_up(local, root, git)
        interpreter_store.collect()
    return Result(
        "activate", code, message, changed=changed, next_steps=_patch_steps(local)
    )


def _patch_steps(local: Path) -> tuple[str, ...]:
    """Name every patch that cleanup kept, on every activation until it is deleted.

    Listing PATCHES after the last cleanup, rather than what this run moved,
    also names a patch that an earlier, failed activation moved.
    """
    try:
        names = sorted(
            entry.name
            for entry in os.scandir(local / PATCHES)
            if entry.is_file(follow_symlinks=False)
        )
    except OSError:
        return ()
    return tuple(
        f"pre-commit saved unstaged edits in {path} that the worktree does not "
        f"match. If a stopped commit lost them, run "
        f"git apply --whitespace=nowarn {path}. "
        f"Then delete {path}."
        for path in (local / PATCHES / name for name in names)
    )


def _healthy(worktree: Worktree, locked: lock.Locked) -> bool:
    try:
        record = require_prepared(worktree, locked, content=True)
        _probe_tools(record, locked)
    except GatesealError:
        return False  # pytestmut: skip[return.none] -- the caller tests its truth
    return interpreter_store.linked(record.attempt, record.trees)


def _plan(locked: lock.Locked, git: Path, wheel: Path | None) -> Plan:
    target = TARGETS.get((platform.system(), platform.machine()))
    if target is None:
        raise GatesealError(
            "unsupported_platform", "Tested targets are macOS arm64 and Linux x64."
        )
    if wheel is None and locked.artifact.kind == "local-wheel":
        wheel = verify_wheel(Path(locked.artifact.location), locked.artifact)
    uv = find_tool("uv")
    _require_uv(uv, locked.root)
    environment = uv_environment(locked.selection.tools)
    if "UV_PYTHON_CACHE_DIR" not in environment:
        cache = execute([uv, "cache", "dir", "--no-config"], locked.root, environment)
        archives = Path(cache.stdout.decode().strip()) / "gateseal-python-archives"
        environment["UV_PYTHON_CACHE_DIR"] = str(archives)
    source = wheel.as_uri() if wheel else locked.artifact.location
    python = interpreter_store.Archive(load_runtime().interpreter(target))
    hook = locked.selection.python
    hook_python = (
        None
        if hook is None
        else (hook.version, interpreter_store.Archive(hook.interpreter(target)))
    )
    tools = locked.selection.tools
    mise = release_binary.plan(tools, target, tool_environment(tools))
    return Plan(locked, git, uv, environment, python, hook_python, source, mise)


def _require_uv(uv: str, root: Path) -> None:
    """Refuse a uv that reports a release older than minimum_uv, or none."""
    reported = execute([uv, "--version"], root, tool_environment())
    minimum = load_runtime().minimum_uv
    try:
        found = reported.stdout.decode().split()[1]
        supported = version_key(found) >= version_key(minimum)
    except IndexError, GatesealError:
        supported = False
    if not supported:
        raise GatesealError(
            "uv_version_mismatch",
            f"gateseal {__version__} requires uv {minimum} or later.",
            4,
        )


def _probe_tools(record: bootstrap.PreparedRecord, locked: lock.Locked) -> list[str]:
    """Probe every prepared tool of the Lock; return the hook tools' versions."""
    tools = locked.selection.tools
    env = pre_commit_environment(record.paths, tools)
    versions = pre_commit_repository.probe(tools, record, locked.root, env)
    release_binary.probe(tools, record, locked.root, env)
    return versions


def _prepare(plan: Plan, local: Path) -> Path:
    """Build one complete attempt and return it; its record is written last."""
    root, uv = plan.locked.root, plan.uv
    attempt = local / ("attempt-" + uuid.uuid4().hex)
    attempt.mkdir(mode=0o700)
    shared = interpreter_store.acquire(plan.python, attempt, uv, root, plan.environment)
    stores = [shared]
    hook_python = None
    if plan.hook_python is not None:
        version, archive = plan.hook_python
        stores.append(
            interpreter_store.acquire(archive, attempt, uv, root, plan.environment)
        )
        hook_python = bootstrap.PreparedTool(version, stores[-1].executable)
    environment = {**plan.environment, "UV_PYTHON_INSTALL_DIR": str(shared.root)}
    interpreter = str(shared.executable)
    compiler_requirements = attempt / "compiler.txt"
    write_atomic(
        compiler_requirements,
        f"gateseal @ {plan.compiler_source} "
        f"--hash=sha256:{plan.locked.artifact.sha256}\n".encode(),
    )
    pre_commit_file = attempt / "pre-commit.txt"
    write_atomic(pre_commit_file, load_runtime().requirements())
    compiler_env, pre_commit_env = attempt / "compiler", attempt / "pre-commit"
    with compiler_download(plan.locked.artifact):
        compiler = package_environment(
            uv, root, environment, interpreter, compiler_env, compiler_requirements
        )
    pre_commit = package_environment(
        uv, root, environment, interpreter, pre_commit_env, pre_commit_file
    )
    execute(
        [compiler, "-I", "-B", "-m", "gateseal", "--repo", str(root), "check"],
        root,
        environment,
    )
    locate = "import gateseal.bootstrap as b; print(b.__file__)"
    found = execute([compiler, "-I", "-B", "-c", locate], root, environment)
    paths = bootstrap.PreparedPaths(
        git=plan.git,
        interpreter=shared.executable,
        compiler_python=Path(compiler),
        pre_commit_python=Path(pre_commit),
        bootstrap=Path(found.stdout.decode().strip()).resolve(),
        pre_commit_home=attempt / "pre-commit-home",
        tools=attempt / "tools",
    )
    paths.tools.mkdir()
    if hook_python is not None:
        name = bootstrap.hook_python_name(hook_python.version)
        (paths.tools / name).symlink_to(hook_python.executable)
    hook_env = pre_commit_environment(paths, plan.locked.selection.tools)
    execute(
        pre_commit_command(pre_commit, ["validate-config", bootstrap.CONFIG]),
        root,
        hook_env,
    )
    hooks, hook_environments = hook_store.prepare(
        plan.locked.selection,
        attempt,
        paths,
        tuple(stores),
        uv,
        root,
        environment,
        hook_env,
    )
    tools = (
        {}
        if plan.mise is None
        else release_binary.install(plan.mise, attempt, paths, root, hook_env)
    )
    installs = tuple(location for location, _ in tools.values())
    entry = attempt / bootstrap.ENTRY
    entry.mkdir()
    # The store is machine-wide, so a copied clone reaches the same interpreter;
    # the relative bootstrap link keeps it inside its copy, where the repository
    # check stops it.
    (entry / bootstrap.PYTHON).symlink_to(paths.interpreter)
    (entry / bootstrap.BOOTSTRAP).symlink_to(os.path.relpath(paths.bootstrap, entry))
    owned = (compiler_env, pre_commit_env, *installs)
    roots = (*(store.root for store in stores), *owned)
    record = bootstrap.PreparedRecord(
        attempt=attempt,
        root=root,
        lock_sha256=sha(plan.locked.snapshot.lock),
        platform=platform.system() + "-" + platform.machine(),
        paths=paths,
        outputs=tuple(plan.locked.snapshot.outputs),
        native_tools={name: tool for name, (_, tool) in tools.items()},
        hook_python=hook_python,
        trees={
            **{store.root: store.digests for store in stores},
            **{
                path: bootstrap.TreeDigests(
                    metadata=bootstrap.tree_digest(path, roots, content=False),
                    content=bootstrap.tree_digest(path, roots, content=True),
                )
                for path in owned
            },
            **hooks,
        },
        hook_environments=hook_environments,
        replaces=_current_name(local),
    )
    write_atomic(attempt / bootstrap.RECORD, canonical(record.document()))
    return attempt


def _publish(worktree: Worktree, locked: lock.Locked, attempt: Path) -> tuple[str, ...]:
    """Install the shared hook if needed, then make attempt current with one rename.

    The hook goes first: a crash between the two steps leaves a hook that fails
    closed through the old current, or through none, and never a published
    attempt without a hook. Every release writes the same hook bytes, so two
    worktrees that activate at once leave the same file in either order.
    """
    root, local = worktree.root, worktree.preparation
    if lock.load(root, local).snapshot != locked.snapshot:
        raise GatesealError(
            "concurrent_change", "Tracked policy changed during activation."
        )
    require_owned_hook(worktree)
    changed: list[str] = []
    hook = worktree.hook
    if not hook.is_file() or not os.access(hook, os.X_OK):
        write_atomic(hook, bootstrap.HOOK, 0o755)
        changed.append(worktree.display(hook))
    with open_directory(local) as directory:
        temporary = ".gateseal-" + uuid.uuid4().hex
        os.symlink(attempt.name, temporary, dir_fd=directory)
        os.replace(
            temporary, bootstrap.CURRENT, src_dir_fd=directory, dst_dir_fd=directory
        )
        # pytestmut: off[stmt.call_remove] -- only a power loss tells
        os.fsync(directory)
        # pytestmut: on
    changed.append(worktree.display(local / bootstrap.CURRENT))
    return tuple(changed)


def _clean_up(local: Path, root: Path, git: str) -> None:
    """Delete every attempt that no commit can still use.

    The current attempt stays, and so does the one it replaced: a commit that
    started before the last publish runs from it, and pre-commit keeps its
    unstaged-changes patch in that attempt's pre-commit home. Before an attempt
    is deleted, _keep_patches moves each patch whose edits the worktree lacks
    to PATCHES, since an interrupted commit may have left the only copy of the
    user's unstaged edits there. It deletes each other patch once
    PATCH_GRACE_SECONDS have passed since pre-commit wrote it, and the attempt
    stays until then. A current that is not a link, as a copy that dereferenced
    links leaves, is removed too, since publishing cannot replace a directory.
    Entries are removed through the directory descriptor and links are never
    followed.

    Every commit holds ATTEMPTS_LOCK shared until its pre-commit exits, so
    cleanup deletes nothing while one runs.
    """
    current = _current_name(local)
    keep = {current}
    if current is not None:
        with contextlib.suppress(bootstrap.BootstrapError, OSError):
            keep.add(bootstrap.PreparedRecord.read(local / current).replaces)
    with open_directory(local) as directory:
        descriptor = bootstrap.open_lock(bootstrap.ATTEMPTS_LOCK, directory)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            for entry in os.scandir(directory):
                if not _stale(entry, keep):
                    continue
                name = entry.name
                if entry.is_dir(follow_symlinks=False):
                    with contextlib.suppress(OSError):
                        if _keep_patches(directory, name, root, git):
                            shutil.rmtree(name, dir_fd=directory, ignore_errors=True)
                else:
                    with contextlib.suppress(OSError):
                        os.unlink(name, dir_fd=directory)
        finally:
            os.close(descriptor)


def _stale(entry: os.DirEntry[str], keep: set[str | None]) -> bool:
    name = entry.name
    if name == bootstrap.CURRENT:
        return not entry.is_symlink()
    return name not in keep and bool(
        bootstrap.ATTEMPT.fullmatch(name) or name.startswith(".gateseal-")
    )


def _keep_patches(directory: int, attempt: str, root: Path, git: str) -> bool:
    """Move pre-commit's unstaged-changes patches out of attempt into PATCHES.

    pre-commit applies its patch when a commit ends and never deletes it, so a
    patch whose result the worktree holds exactly is deleted instead, once
    PATCH_GRACE_SECONDS have passed since pre-commit wrote it. pre-commit clears
    the edits with a git checkout child that does not hold the attempts lock,
    so after a SIGKILL of pre-commit that child can still clear edits that the
    worktree holds, and a file it is still writing can match the patch. Return
    whether attempt can be deleted, which is False while such a patch waits.
    An OSError means a patch may still be inside, so the caller keeps attempt.
    """
    try:
        home = os.open(
            attempt + "/pre-commit-home",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=directory,
        )
    except OSError:
        return True
    try:
        patches = _patches(home)
        lost = [patch for patch in patches if not _restored(home, patch, root, git)]
        cutoff = time.time() - PATCH_GRACE_SECONDS
        waiting = [patch for patch in patches if patches[patch] > cutoff]
        for patch in patches:
            if patch not in lost and patch not in waiting:
                os.unlink(patch, dir_fd=home)
        if lost:
            _move_patches(directory, home, attempt, lost)
        return all(patch in lost for patch in waiting)
    finally:
        os.close(home)


def _patches(home: int) -> dict[str, float]:
    """Return when pre-commit wrote each unstaged-changes patch in home."""
    return {
        # pytestmut: off[bool.literal] -- the filter below admits only files
        entry.name: entry.stat(follow_symlinks=False).st_mtime
        # pytestmut: on
        for entry in os.scandir(home)
        if entry.name.startswith("patch") and entry.is_file(follow_symlinks=False)
    }


def _move_patches(directory: int, home: int, attempt: str, lost: list[str]) -> None:
    with contextlib.suppress(FileExistsError):
        os.mkdir(PATCHES, 0o700, dir_fd=directory)
    target = os.open(
        PATCHES, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory
    )
    try:
        for patch in lost:
            name = f"{attempt}-{patch}"
            os.rename(patch, name, src_dir_fd=home, dst_dir_fd=target)
    finally:
        os.close(target)


def _restored(home: int, patch: str, root: Path, git: str) -> bool:
    """Return whether pre-commit restored patch and the worktree still holds it.

    git apply --check --reverse is not proof: with repeated lines it matches the
    edits' context at an offset in a worktree that lost them. Instead, patch is
    applied to a scratch index of its own pre-image, and refresh compares every
    resulting file with the worktree by content and mode, as git status does.
    A file that patch deletes must be absent.

    Any git failure, such as a pre-image blob that gc pruned, keeps the patch.
    A mode-only change names no blob, so the pre-image is built from the
    repository's index. --whitespace=nowarn stops apply.whitespace=fix from
    stripping the edits being checked, as pre-commit stops it when it restores
    them. fsmonitor and ignoreStat are off because either could report a scratch
    entry unchanged without reading the worktree.
    """
    # pytestmut: off[bit.and_or] -- the caller found a regular file
    descriptor = os.open(patch, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=home)
    # pytestmut: on
    with os.fdopen(descriptor, "rb") as handle:
        data = handle.read()
    with tempfile.TemporaryDirectory() as scratch:
        index = os.path.join(scratch, "index")
        repository = tool_environment()
        isolated = dict(repository, GIT_INDEX_FILE=index)
        options = ["-c", "core.fsmonitor=false", "-c", "core.ignoreStat=false"]
        apply = ["apply", "--whitespace=nowarn"]

        def run(
            env: dict[str, str], *arguments: str, stdin: bytes | None = None
        ) -> bytes | None:
            command = [git, *options, *arguments]
            result = execute(command, root, env, allow_failure=True, stdin=stdin)
            return None if result.returncode else result.stdout

        ancestor = [*apply, f"--build-fake-ancestor={index}", "-"]
        if run(repository, *ancestor, stdin=data) is None:
            return False  # pytestmut: skip[return.none] -- the caller tests its truth
        before = run(isolated, "ls-files", "-z")
        if before is None or run(isolated, *apply, "--cached", "-", stdin=data) is None:
            return False  # pytestmut: skip[return.none] -- the caller tests its truth
        after = run(isolated, "ls-files", "-z")
        if after is None:
            # pytestmut: off[bool.literal, return.*] -- apply just wrote that index
            return False
            # pytestmut: on
        deleted = _listed(before) - _listed(after)
        if any(os.path.lexists(root / name) for name in deleted):
            return False  # pytestmut: skip[return.none] -- the caller tests its truth
        return run(isolated, "update-index", "--refresh") is not None


def _listed(output: bytes) -> set[str]:
    """Return the paths that git ls-files -z printed."""
    return {os.fsdecode(name) for name in output.split(b"\0") if name}


def _current_name(local: Path) -> str | None:
    try:
        name = os.readlink(local / bootstrap.CURRENT)
    except OSError:
        return None
    return name if bootstrap.ATTEMPT.fullmatch(name) else None


def require_prepared(
    worktree: Worktree, locked: lock.Locked, *, content: bool = False
) -> bootstrap.PreparedRecord:
    local = preparation_directory(worktree)
    hook = worktree.hook
    try:
        require_owned_hook(worktree)
        name = _current_name(local)
        if name is None:
            raise bootstrap.BootstrapError(
                "No preparation is published for this worktree."
            )
        record = bootstrap.PreparedRecord.read(local / name)
        if record.root != locked.root:
            raise bootstrap.BootstrapError(
                f"This preparation belongs to {record.root}."
            )
        if record.lock_sha256 != sha(locked.snapshot.lock):
            raise bootstrap.BootstrapError("Tracked lock differs from preparation.")
        if not hook.is_file() or not os.access(hook, os.X_OK):
            raise bootstrap.BootstrapError(
                "The gateseal hook is missing or not executable."
            )
        bootstrap.verify_runtime(record, content=content)
        return record
    except (bootstrap.BootstrapError, OSError) as exc:
        raise GatesealError(
            "needs_activation", str(exc), 3, "gateseal activate"
        ) from exc


def doctor(worktree: Worktree, locked: lock.Locked) -> Result:
    """Check the seals and hook executables, and name the compiler commits run.

    The prepared compiler answers --version offline, as a commit runs it, so the
    details name the compiler of this worktree's commits.
    """
    record = require_prepared(worktree, locked, content=True)
    tools = _probe_tools(record, locked)
    compiler = execute(
        [str(record.paths.compiler_python), "-I", "-B", "-m", "gateseal", "--version"],
        locked.root,
        tool_environment(),
    )
    result = execute(
        pre_commit_command(str(record.paths.pre_commit_python), ["--version"]),
        locked.root,
        pre_commit_environment(record.paths, locked.selection.tools),
    )
    notes = lock.describe(locked)
    return Result(
        "doctor",
        "healthy",
        "Runtime seals and native hook executable probes passed. "
        "Hook cache inspection is not a network fence.",
        next_steps=lock.review_steps(notes),
        details=f"gateseal {compiler.stdout.decode().strip()}\n"
        + result.stdout.decode()
        + "".join(f"{line}\n" for line in tools),
        notes=notes,
    )


@contextlib.contextmanager
def pinned(
    worktree: Worktree, locked: lock.Locked
) -> Generator[bootstrap.PreparedRecord]:
    """Pin the published attempt for as long as the caller runs from it.

    The shared attempts lock is taken before the record is read, as a commit's
    bootstrap takes it, so cleanup cannot delete the attempt in between or
    until the caller is done.
    """
    descriptor = bootstrap.open_lock(
        preparation_directory(worktree, create=True) / bootstrap.ATTEMPTS_LOCK
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        yield require_prepared(worktree, locked)
    finally:
        os.close(descriptor)


def exec_pre_commit(
    record: bootstrap.PreparedRecord, tools: Iterable[Tool]
) -> NoReturn:
    os.chdir(record.root)
    python = str(record.paths.pre_commit_python)
    os.execve(
        python,
        pre_commit_command(python, hook_run(Stage.COMMIT)),
        pre_commit_environment(record.paths, tools),
    )


def status(worktree: Worktree, locked: lock.Locked) -> Result:
    notes = lock.describe(locked)
    try:
        require_prepared(worktree, locked)
    except GatesealError as exc:
        return Result(
            "status",
            exc.code,
            str(exc),
            next_steps=(
                *((exc.next_step,) if exc.next_step else ()),
                *lock.review_steps(notes),
            ),
            notes=notes,
        )
    return Result(
        "status",
        "recorded",
        "Tracked policy matches. Preparation is recorded; "
        "run doctor to verify runtime health.",
        next_steps=lock.review_steps(notes),
        notes=notes,
    )
