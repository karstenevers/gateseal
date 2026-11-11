"""Pre-commit entry point that checks staged policy before loading the compiler.

The hook runs this file as ``python -I -B -S``, before the selected compiler is
imported, so it uses only the standard library and never imports ``gateseal``.
The rest of the package imports its shared names and helpers from here.
"""

import fcntl
import hashlib
import json
import math
import os
import platform
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import NoReturn, Self, cast

MANIFEST = "gateseal.toml"
LOCK = "gateseal.lock"
CONFIG = ".pre-commit-config.yaml"

PREPARATION = "gateseal"
"""A worktree's preparation directory, inside its own Git directory.

Git treats this name as per-worktree, so `git rev-parse --git-path gateseal`
names the directory the hook reaches as "${GIT_DIR:-.git}/gateseal".
"""
CURRENT = "current"
ATTEMPT = re.compile(r"attempt-[0-9a-f]{32}")
RECORD = "prepared.json"
ATTEMPTS_LOCK = "attempts.lock"
ENTRY = "hook"
PYTHON = "python"
BOOTSTRAP = "bootstrap.py"
TOOL_PREFIX = "gateseal-"

ACTIVATE = "Run gateseal activate, then retry."
CHECK = "Run gateseal check, fix what it reports, stage the files, then retry."

MINIMUM_GIT = "2.54.0"
"""The oldest Git that gateseal accepts. Git 2.54.0 added `git hook list`, which
gateseal runs to read the hooks that Git's configuration defines."""

# `cd -P` and `pwd -P` pin one physical attempt, so every stage of a commit runs
# from the same attempt even if activation publishes another meanwhile. Git runs
# the hook at the top of the worktree that commits and exports GIT_DIR whenever
# that worktree's .git is a file, as in a linked worktree, so "${GIT_DIR:-.git}"
# is that worktree's own Git directory. The bytes name no path and no release,
# so every worktree and every compiler shares them. GIT_DIR and GIT_INDEX_FILE
# pass through only when Git set them, so the bootstrap and pre-commit see the
# repository and index Git chose. CDPATH is cleared because a path that does
# not start with "./" would consult it. -L refuses an attempt copied with its
# links dereferenced.
HOOK = rb"""#!/bin/sh
# gateseal pre-commit hook. gateseal activate owns this file; edits make it unmanaged.
attempt=$(CDPATH= cd -P "${GIT_DIR:-.git}/gateseal/current" 2>/dev/null && pwd -P)
python="$attempt/hook/python"
bootstrap="$attempt/hook/bootstrap.py"
if [ -z "$attempt" ] || [ ! -L "$python" ] || [ ! -f "$python" ] ||
  [ ! -x "$python" ] || [ ! -f "$bootstrap" ]; then
  echo "gateseal: This worktree has no usable preparation." >&2
  echo "Next: Run gateseal activate, then retry." >&2
  exit 3
fi
exec /usr/bin/env -i HOME="${HOME-}" LANG="${LANG-C.UTF-8}" \
  TMPDIR="${TMPDIR-/tmp}" PATH=/usr/bin:/bin \
  ${GIT_DIR+"GIT_DIR=$GIT_DIR"} \
  ${GIT_INDEX_FILE+"GIT_INDEX_FILE=$GIT_INDEX_FILE"} \
  "$python" -I -B -S "$bootstrap" "$attempt"
"""


class BootstrapError(Exception):
    """A local preparation or staged-input failure."""

    def __init__(self, message: str, next_step: str = ACTIVATE) -> None:
        super().__init__(message)
        self.next_step = next_step


class _GitError(BootstrapError):
    """A failed git call, with Git's own stderr kept for a caller's message."""

    def __init__(self, detail: str) -> None:
        super().__init__(
            f"Git could not read the index: {detail}",
            "Resolve the Git error above, then retry.",
        )
        self.detail = detail


class JSONError(ValueError):
    """A strict-JSON violation; each caller translates it into its own error."""


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(k, str) for k in cast(dict[object, object], value)
    ):
        raise BootstrapError("Expected a JSON object.")
    return cast(dict[str, object], value)


def _text(value: object) -> str:
    if not isinstance(value, str):
        raise BootstrapError("Expected a JSON string.")
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise JSONError(f"Duplicate JSON key {key!r}.")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise JSONError(f"Non-finite JSON number {value} is unsupported.")


def _finite(text: str) -> float:
    value = float(text)
    return value if math.isfinite(value) else _constant(text)


def decode_json_value(data: bytes) -> object:
    """Decode UTF-8 JSON, rejecting duplicate keys and nonfinite numbers."""
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_constant,
            parse_float=_finite,
        )
    except JSONError:
        raise
    except ValueError as exc:
        raise JSONError("Expected UTF-8 JSON.") from exc


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def open_lock(name: str | Path, dir_fd: int | None = None) -> int:
    """Open lock file name read-write without following a link, creating it.

    On macOS an O_CREAT open can fail with ENOENT while another process
    creates the same file, so a missing lock is created with O_EXCL and opened
    again when another process wins.
    """
    flags = os.O_RDWR | os.O_NOFOLLOW
    while True:
        try:
            return os.open(name, flags, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        try:
            return os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            pass


def tracked_bytes(data: bytes) -> bytes:
    """Return a tracked file's bytes as Git stores them.

    With core.autocrlf, Git checks these files out with CRLF line endings but
    stores and compares them with LF, so gateseal reads them the same way.
    """
    return data.replace(b"\r\n", b"\n")


def tree_digest(root: Path, allowed_roots: tuple[Path, ...], *, content: bool) -> str:
    if root.is_symlink() or not root.is_dir():
        raise BootstrapError("Prepared runtime directory is missing or replaced.")
    hasher = hashlib.sha256(b"gateseal-runtime-tree-v1\0")
    for directory, directories, filenames in os.walk(root, followlinks=False):
        directories[:] = sorted(directories)
        for name in sorted([*directories, *filenames]):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            info = path.lstat()
            payload = _payload(path, info, allowed_roots, content=content)
            record = json.dumps(
                [relative, stat.S_IMODE(info.st_mode), payload],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            hasher.update(len(record).to_bytes(8, "big"))
            hasher.update(record)
    return hasher.hexdigest()


def _payload(
    path: Path, info: os.stat_result, allowed_roots: tuple[Path, ...], *, content: bool
) -> str:
    if stat.S_ISLNK(info.st_mode):
        target = path.resolve(strict=True)
        if not any(target.is_relative_to(allowed) for allowed in allowed_roots):
            raise BootstrapError("Runtime symlink escapes verified distributions.")
        return "link:" + os.readlink(path)
    if stat.S_ISREG(info.st_mode):
        return (
            "file:" + file_hash(path)
            if content
            else f"file:{info.st_size}:{info.st_mtime_ns}"
        )
    if stat.S_ISDIR(info.st_mode):
        return "directory"
    raise BootstrapError("Nonregular runtime payload is unsupported.")


def clean_environment() -> dict[str, str]:
    result = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                "PYTHON",
                "UV_",
                "PIP_",
                "VIRTUALENV_",
                "CONDA",
                "DYLD_",
                "MISE_",
            )
        )
        and key
        not in ("VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "LD_PRELOAD", "LD_LIBRARY_PATH")
    }
    result.update(
        {
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    return result


def require_git(git: Path, root: Path) -> None:
    """Refuse a git that reports a release older than MINIMUM_GIT, or none.

    git answers in root, because a shim such as mise's picks its Git by folder.
    """
    printed = subprocess.run(
        [git, "--version"],
        cwd=root,
        capture_output=True,
        env=clean_environment(),
        check=False,
    ).stdout
    found = re.match(rb"git version ([0-9]+)\.([0-9]+)\.([0-9]+)", printed)
    release = tuple(int(part) for part in found.groups()) if found else ()
    if release >= tuple(int(part) for part in MINIMUM_GIT.split(".")):
        return
    version = "Git " + ".".join(map(str, release)) if release else "no Git version"
    raise BootstrapError(
        f"{git} reports {version}, and gateseal requires Git {MINIMUM_GIT} or later.",
        f"Install Git {MINIMUM_GIT} or later, run gateseal activate, then retry.",
    )


def _strings(value: object, names: set[str], message: str) -> dict[str, str]:
    document = _object(value)
    if set(document) != names:
        raise BootstrapError(message)
    return {name: _text(item) for name, item in document.items()}


@dataclass(frozen=True)
class PreparedPaths:
    git: Path
    interpreter: Path
    compiler_python: Path
    pre_commit_python: Path
    bootstrap: Path
    pre_commit_home: Path
    tools: Path


@dataclass(frozen=True)
class PreparedTool:
    version: str
    executable: Path


def hook_python_name(version: str) -> str:
    """Return the Hook Python's name in the tools directory and its hooks' config."""
    return PYTHON + version


@dataclass(frozen=True)
class TreeDigests:
    metadata: str
    content: str


@dataclass(frozen=True)
class HookEnvironment:
    """A pre-commit hook environment and the Lock component it was built from.

    executables are the programs its hooks run. pre-commit finds them on a
    PATH that ends with the host's, so a missing one would run a host program.
    """

    component: str
    python: Path
    marker_sha256: str
    executables: tuple[Path, ...]


def _check_protocol(document: dict[str, object], names: set[str]) -> str | None:
    replaces = document.get("replaces")
    if (
        set(document) != names | {"schema"}
        or type(document["schema"]) is not int
        or document["schema"] != 1
        or not (replaces is None or isinstance(replaces, str))
    ):
        raise BootstrapError("Unsupported prepared record protocol.")
    return replaces


def _native_tools(records: object) -> dict[str, PreparedTool]:
    tools: dict[str, PreparedTool] = {}
    for name, value in _object(records).items():
        entry = _strings(
            value, {"version", "executable"}, "Unsupported native tool record."
        )
        if (
            not re.fullmatch("[a-z][a-z0-9-]*", name)
            or not Path(entry["executable"]).is_absolute()
        ):
            raise BootstrapError("Unsupported native tool record.")
        tools[name] = PreparedTool(entry["version"], Path(entry["executable"]))
    return tools


def _hook_python(value: object) -> PreparedTool | None:
    if value is None:
        return None
    entry = _strings(
        value, {"version", "executable"}, "Unsupported Hook Python record."
    )
    if (
        not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", entry["version"])
        or not Path(entry["executable"]).is_absolute()
    ):
        raise BootstrapError("Unsupported Hook Python record.")
    return PreparedTool(entry["version"], Path(entry["executable"]))


def _hook_environments(records: object) -> dict[Path, HookEnvironment]:
    environments: dict[Path, HookEnvironment] = {}
    for name, value in _object(records).items():
        entry = _object(value)
        executables = entry.pop("executables", None)
        if not isinstance(executables, list) or not all(
            isinstance(path, str) and Path(path).is_absolute()
            for path in cast(list[object], executables)
        ):
            raise BootstrapError("Unsupported hook environment record.")
        entry = _strings(
            entry,
            {"component", "python", "marker_sha256"},
            "Unsupported hook environment record.",
        )
        environments[Path(name)] = HookEnvironment(
            entry["component"],
            Path(entry["python"]),
            entry["marker_sha256"],
            tuple(Path(path) for path in cast(list[str], executables)),
        )
    return environments


def _check_seals(
    trees: dict[Path, TreeDigests],
    tools: dict[str, PreparedTool],
    hook_python: PreparedTool | None,
    environments: dict[Path, HookEnvironment],
) -> None:
    # A hook environment lives in a store entry of its own, sealed like the
    # interpreter, so the record needs one more seal per entry.
    entries = {
        next((tree for tree in trees if name.is_relative_to(tree)), None)
        for name in environments
    }
    expected = 3 + len(tools) + (hook_python is not None) + len(entries)
    if None in entries or len(trees) != expected:
        raise BootstrapError(
            "Expected interpreter, compiler, pre-commit, Hook Python, native "
            "tool, and hook environment seals."
        )


@dataclass(frozen=True)
class PreparedRecord:
    """One complete attempt, stored as its prepared.json.

    attempt is the directory holding the record and is never serialized. A
    copied or moved clone carries a record whose absolute paths name the
    original, so nothing may trust them before the repository check.
    """

    attempt: Path
    root: Path
    lock_sha256: str
    platform: str
    paths: PreparedPaths
    outputs: tuple[str, ...]
    native_tools: dict[str, PreparedTool]
    hook_python: PreparedTool | None
    trees: dict[Path, TreeDigests]
    hook_environments: dict[Path, HookEnvironment]
    replaces: str | None

    @classmethod
    def read(cls, attempt: Path) -> Self:
        path = attempt / RECORD
        if path.is_symlink() or not path.is_file():
            raise BootstrapError(f"No complete preparation in {attempt}.")
        try:
            document = _object(decode_json_value(path.read_bytes()))
        except JSONError as exc:
            raise BootstrapError(f"Prepared record {path} is invalid: {exc}") from exc
        replaces = _check_protocol(
            document, {field.name for field in fields(cls)} - {"attempt"}
        )
        names = {field.name for field in fields(PreparedPaths)}
        paths = _strings(document["paths"], names, "Unsupported prepared paths.")
        if not all(Path(value).is_absolute() for value in paths.values()):
            raise BootstrapError("Prepared paths must be absolute.")
        outputs = document["outputs"]
        if not isinstance(outputs, list) or not all(
            isinstance(name, str) and name for name in cast(list[object], outputs)
        ):
            raise BootstrapError("Unsupported prepared outputs.")
        tools = _native_tools(document["native_tools"])
        hook_python = _hook_python(document["hook_python"])
        trees = {
            Path(name): TreeDigests(
                **_strings(value, {"metadata", "content"}, "Unsupported runtime seal.")
            )
            for name, value in _object(document["trees"]).items()
        }
        environments = _hook_environments(document["hook_environments"])
        _check_seals(trees, tools, hook_python, environments)
        return cls(
            attempt=attempt,
            root=Path(_text(document["root"])),
            lock_sha256=_text(document["lock_sha256"]),
            platform=_text(document["platform"]),
            paths=PreparedPaths(**{name: Path(value) for name, value in paths.items()}),
            outputs=tuple(cast(list[str], outputs)),
            native_tools=tools,
            hook_python=hook_python,
            trees=trees,
            hook_environments=environments,
            replaces=replaces,
        )

    def document(self) -> dict[str, object]:
        return {
            "schema": 1,
            "root": str(self.root),
            "lock_sha256": self.lock_sha256,
            "platform": self.platform,
            "paths": {name: str(value) for name, value in asdict(self.paths).items()},
            "outputs": list(self.outputs),
            "native_tools": {
                name: {"version": tool.version, "executable": str(tool.executable)}
                for name, tool in self.native_tools.items()
            },
            "hook_python": None
            if self.hook_python is None
            else {
                "version": self.hook_python.version,
                "executable": str(self.hook_python.executable),
            },
            "trees": {
                str(path): asdict(digests) for path, digests in self.trees.items()
            },
            "hook_environments": {
                str(path): {
                    "component": environment.component,
                    "python": str(environment.python),
                    "marker_sha256": environment.marker_sha256,
                    "executables": [str(path) for path in environment.executables],
                }
                for path, environment in self.hook_environments.items()
            },
            "replaces": self.replaces,
        }


def _check_prepared_files(record: PreparedRecord) -> None:
    paths = record.paths
    for path in (
        paths.git,
        paths.interpreter,
        paths.compiler_python,
        paths.pre_commit_python,
        paths.bootstrap,
    ):
        if not path.is_file():
            raise BootstrapError(f"Prepared file {path} is missing.")
    require_git(paths.git, record.root)


def _check_entry_points(record: PreparedRecord) -> None:
    paths = record.paths
    entry = record.attempt / ENTRY
    links = [(entry / PYTHON, paths.interpreter), (entry / BOOTSTRAP, paths.bootstrap)]
    tools = {TOOL_PREFIX + name: tool for name, tool in record.native_tools.items()}
    if record.hook_python is not None:
        tools[hook_python_name(record.hook_python.version)] = record.hook_python
    for name, tool in tools.items():
        if not tool.executable.is_file():
            raise BootstrapError(f"Prepared file {tool.executable} is missing.")
        links.append((paths.tools / name, tool.executable))
    for link, target in links:
        if link.resolve() != target:
            raise BootstrapError(f"Prepared entry point {link} changed.")
    _check_tools_directory(paths.tools, tools)


def _check_tools_directory(directory: Path, tools: dict[str, PreparedTool]) -> None:
    # The tools directory comes first on the hooks' PATH, so any other entry
    # there could stand in for Git or Python.
    if (
        directory.is_symlink()
        or not directory.is_dir()
        or sorted(os.listdir(directory)) != sorted(tools)
    ):
        raise BootstrapError(f"Prepared tool directory {directory} changed.")


def _check_hook_environment(name: Path, environment: HookEnvironment) -> None:
    marker = name / ".install_state_v1"
    if not environment.python.is_file() or not marker.is_file():
        raise BootstrapError("A pre-commit hook environment is missing.")
    if file_hash(marker) != environment.marker_sha256:
        raise BootstrapError("A pre-commit hook installation record changed.")
    for executable in environment.executables:
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise BootstrapError(
                f"Hook executable {executable} is missing or not executable."
            )


def verify_runtime(record: PreparedRecord, *, content: bool) -> None:
    if record.platform != platform.system() + "-" + platform.machine():
        raise BootstrapError("Prepared platform differs from this machine.")
    _check_prepared_files(record)
    _check_entry_points(record)
    for name, environment in record.hook_environments.items():
        _check_hook_environment(name, environment)
    roots = tuple(record.trees)
    checks = (False, True) if content else (False,)
    for tree, digests in record.trees.items():
        for by_content in checks:
            expected = digests.content if by_content else digests.metadata
            if tree_digest(tree, roots, content=by_content) != expected:
                raise BootstrapError("A prepared runtime or tool payload changed.")


def report(message: str, next_step: str, code: int) -> int:
    print(f"gateseal: {message}\nNext: {next_step}", file=sys.stderr)
    return code


def _git(git: Path, root: Path, arguments: list[str]) -> bytes:
    result = subprocess.run(
        [git, "-C", root, *arguments],
        capture_output=True,
        env=clean_environment(),
        check=False,
    )
    if result.returncode:
        raise _GitError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def read_blob(git: Path, root: Path, oid: str) -> bytes:
    return tracked_bytes(_git(git, root, ["cat-file", "blob", oid]))


def _read_staged(git: Path, root: Path, name: str, oid: str) -> bytes:
    """Return the staged blob of name, or name the file whose blob Git lacks."""
    try:
        return read_blob(git, root, oid)
    except _GitError as exc:
        raise BootstrapError(
            f"Git could not read staged {name}: {exc.detail}",
            f"Stage {name} again with git add, then retry.",
        ) from exc


def _worktree_bytes(root: Path, name: str) -> bytes | None:
    """Return a tracked file's worktree bytes as Git compares them.

    None means the worktree holds no regular file there, which no staged
    content equals.
    """
    path = root / name
    if path.is_symlink() or not path.is_file():
        return None
    return tracked_bytes(path.read_bytes())


def check_repository(record: PreparedRecord) -> None:
    """Stop unless Git commits in the worktree the record was prepared for.

    Git runs the hook at the top of the worktree that commits, and the hook
    found this attempt through that worktree's own Git directory, so the working
    directory alone tells a copied or moved clone or worktree from the one the
    record names. Every index is accepted here; check_staged reads the one Git
    names.
    """
    cwd = Path.cwd().resolve()
    if cwd != record.root:
        raise BootstrapError(
            f"This preparation belongs to {record.root}.",
            f"Run gateseal activate in {cwd}.",
        )


def _staged_oid(name: str, entries: list[list[str]]) -> str:
    [mode, oid, stage], *others = entries
    if others or stage != "0":
        raise BootstrapError(
            f"{name} is conflicted in the index.",
            f"Resolve the conflict in {name}, stage it, then retry.",
        )
    if mode != "100644":
        raise BootstrapError(
            f"{name} has an unsupported staged type {mode}.",
            f"Stage {name} as a regular, non-executable file, then retry.",
        )
    return oid


def _index_entries(
    listing: bytes, tracked: tuple[str, ...]
) -> dict[str, list[list[str]]]:
    """Return each tracked name's mode, blob ID and stage in ls-files --stage."""
    entries: dict[str, list[list[str]]] = {name: [] for name in tracked}
    for item in filter(None, listing.decode(errors="replace").split("\0")):
        # pytestmut: off[num.shift] -- owned output names hold no tab
        descriptor, name = item.split("\t", 1)
        # pytestmut: on
        if name in entries:
            entries[name].append(descriptor.split(" "))
    return entries


def _check_staged_lock(data: bytes) -> None:
    try:
        lock = decode_json_value(data)
    except JSONError as exc:
        raise BootstrapError(
            f"Staged {LOCK} is not a valid Lock: {exc}", CHECK
        ) from exc
    document = cast(dict[str, object], lock) if isinstance(lock, dict) else {}
    if type(document.get("schema")) is not int or document["schema"] != 1:
        raise BootstrapError(f"Staged {LOCK} has an unsupported schema.", CHECK)


def _check_prepared_lock(record: PreparedRecord, staged: dict[str, bytes]) -> None:
    if sha(staged[LOCK]) == record.lock_sha256:
        return
    differing = [
        name
        for name in (MANIFEST, LOCK)
        if _worktree_bytes(record.root, name) != staged[name]
    ]
    if not differing:
        raise BootstrapError(f"Staged {LOCK} differs from the prepared runtime.")
    files = " and ".join(differing)
    raise BootstrapError(
        f"Staged {LOCK} differs from the prepared runtime, "
        f"and the worktree does not hold the staged {files}.",
        f"Stage or restore {files}, then retry.",
    )


def check_staged(record: PreparedRecord) -> list[str]:
    """Check the index this commit records against the prepared Lock.

    The index is the one GIT_INDEX_FILE names: Git's temporary index for -a,
    -i, -p and paths, a user's alternate index, or the worktree's own. Nothing
    falls back to another index or to the worktree. The staged Lock alone
    selects the compiler, so unstaged Manifest or Lock edits never change which
    compiler runs. The staged owned outputs must equal the worktree's, because
    pre-commit and Betterleaks read the worktree's files.

    This runs before the compiler is imported, so a staged Lock that selects
    another compiler never reaches it. "activate" is offered only when the
    worktree's Manifest and Lock equal the staged ones, because activation
    prepares the worktree's files. The record's outputs are required only once
    the staged Lock is the prepared one, because another Lock can select other
    outputs. Returns the blob IDs of the Manifest, the Lock and the record's
    outputs, in that order.
    """
    root, git = record.root, record.paths.git
    tracked = (MANIFEST, LOCK, *record.outputs)
    listing = _git(git, root, ["ls-files", "--stage", "-z", "--", *tracked])
    entries = _index_entries(listing, tracked)
    missing = BootstrapError(
        "Missing from the commit's index: "
        f"{', '.join(name for name in tracked if not entries[name])}.",
        f"Stage {', '.join(tracked[:-1])} and {tracked[-1]} together. On a "
        "branch without gateseal, merge or rebase a branch that has them, or "
        "commit with --no-verify.",
    )
    if not entries[MANIFEST] or not entries[LOCK]:
        raise missing
    oids = {name: _staged_oid(name, entries[name]) for name in (MANIFEST, LOCK)}
    staged = {name: _read_staged(git, root, name, oid) for name, oid in oids.items()}
    _check_staged_lock(staged[LOCK])
    _check_prepared_lock(record, staged)
    if not all(entries[name] for name in record.outputs):
        raise missing
    for name in record.outputs:
        oids[name] = _staged_oid(name, entries[name])
        if _worktree_bytes(root, name) != _read_staged(git, root, name, oids[name]):
            raise BootstrapError(
                f"Unstaged policy edits in {name}.",
                f"Stage or restore {name}, then retry.",
            )
    return list(oids.values())


def main(argv: list[str] | None = None) -> int:
    """Gate one commit, then become the prepared compiler's _staged.

    Returns only on failure. Every later stage replaces this process, so no
    Python parent waits on pre-commit, and Ctrl-C reaches pre-commit without
    a parent that kills it while it restores unstaged changes.
    """
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if len(arguments) != 1 or not Path(arguments[0]).is_absolute():
            raise BootstrapError("The hook passed unexpected arguments.")
        attempt = Path(arguments[0])
        # Never closed: the inheritable descriptor survives both execs, so the
        # commit holds this lock until pre-commit exits and cleanup keeps attempt.
        descriptor = open_lock(attempt.parent / ATTEMPTS_LOCK)
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        os.set_inheritable(descriptor, True)
        record = PreparedRecord.read(attempt)
        check_repository(record)
        oids = check_staged(record)
        verify_runtime(record, content=False)
        python = str(record.paths.compiler_python)
        command = [python, "-I", "-B", "-m", "gateseal", "_staged", arguments[0]]
        os.execve(python, [*command, *oids], clean_environment())
    except BootstrapError as exc:
        return report(str(exc), exc.next_step, 3)
    except (OSError, ValueError) as exc:
        return report(str(exc), ACTIVATE, 3)


if __name__ == "__main__":
    raise SystemExit(main())
