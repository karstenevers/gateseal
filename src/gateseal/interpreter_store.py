"""The machine-wide store of interpreters and hook environments that attempts share.

The store is a directory of entries, each named by a SHA-256: an interpreter
by its archive's, a hook environment by its recipe's (see hook_store):

    store.lock                       flock file, never deleted
    <sha256>/                        the entry
    <sha256>.json                    the seal, written once the entry is complete
    <sha256>.refs/attempt-<32 hex>   a link to each attempt that uses the entry

uv and pre-commit write an entry's absolute path into it, so an entry is built
where it stays and never renamed. Its files get read-only modes. An
interpreter's files also get a fixed mtime, so a reinstall at the same path
reproduces the sealed digests and every attempt that recorded them stays valid.
Commits never take the store lock; an attempt's link keeps its entry while the
attempt directory exists.
"""

import contextlib
import fcntl
import os
import re
import shutil
import stat
from collections.abc import Callable, Generator, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from gateseal import bootstrap
from gateseal.diagnostics import GatesealError
from gateseal.downloads import python_download
from gateseal.managed_files import open_directory, read, write_atomic
from gateseal.processes import execute, find_python
from gateseal.validation import canonical

STORE_VARIABLE = "GATESEAL_INTERPRETER_STORE"
LOCK = "store.lock"
SEAL_SCHEMA = 1
FIXED_MTIME_NS = 946_684_800 * 1_000_000_000
"""2000-01-01T00:00:00Z, the mtime of every file in an interpreter entry."""
NAME = re.compile(r"([0-9a-f]{64})(\.json|\.refs)?")


@dataclass(frozen=True)
class Archive:
    """uv's download metadata for one interpreter archive, as one record by key."""

    metadata: dict[str, dict[str, object]]

    @property
    def key(self) -> str:
        """Return uv's key for the archive."""
        return next(iter(self.metadata))

    @property
    def sha256(self) -> str:
        """Return the archive's SHA-256, which names its store entry."""
        return str(self.metadata[self.key]["sha256"])

    @property
    def url(self) -> str:
        return str(self.metadata[self.key]["url"])


@dataclass(frozen=True)
class Interpreter:
    """A sealed store entry: its directory, its resolved python and its digests."""

    root: Path
    executable: Path
    digests: bootstrap.TreeDigests


def location() -> Path:
    """Return where the store is, before it is created or resolved."""
    override = os.environ.get(STORE_VARIABLE)
    if override is not None:
        if not os.path.isabs(override):
            raise GatesealError(
                "unsupported_path",
                f"{STORE_VARIABLE} is {override!r}, which is not an absolute path.",
                next_step=f"Set {STORE_VARIABLE} to an absolute path, or unset it, "
                "then run gateseal activate.",
            )
        return Path(override)
    data = os.environ.get("XDG_DATA_HOME", "")
    base = Path(data) if os.path.isabs(data) else Path.home() / ".local" / "share"
    return base / "gateseal" / "interpreters"


def acquire(
    archive: Archive, attempt: Path, uv: str, root: Path, environment: dict[str, str]
) -> Interpreter:
    """Return archive's sealed entry, installing it first unless it verifies.

    uv inherits the store lock, so a killed activation's uv keeps the store
    locked until it exits.
    """

    def install(entry: Path, lock: int) -> None:
        downloads = attempt / "python-downloads.json"
        write_atomic(downloads, canonical(archive.metadata))
        with python_download(archive.url, environment["UV_PYTHON_CACHE_DIR"]):
            execute(
                [uv, "python", "install", "--no-config", "--no-bin"]
                + ["--install-dir", str(entry)]
                + ["--python-downloads-json-url", str(downloads), archive.key],
                root,
                {**environment, "UV_PYTHON_INSTALL_DIR": str(entry)},
                inherit=(lock,),
            )

    identity: dict[str, object] = {"key": archive.key}
    with sealed(archive.sha256, identity, attempt, install) as (entry, digests):
        env = {**environment, "UV_PYTHON_INSTALL_DIR": str(entry)}
        executable = Path(find_python(uv, root, env, archive.key)).resolve()
    if not executable.is_relative_to(entry):
        raise GatesealError(
            "invalid_preparation",
            f"uv found {executable}, which is not in the store entry {entry}.",
            4,
        )
    return Interpreter(entry, executable, digests)


@contextlib.contextmanager
def sealed(
    name: str,
    identity: dict[str, object],
    attempt: Path,
    build: Callable[[Path, int], None],
    roots: tuple[Path, ...] = (),
    reproducible: bool = True,
) -> Generator[tuple[Path, bootstrap.TreeDigests]]:
    """Hold entry name sealed for identity, building it first unless it verifies.

    attempt links to the entry before the entry is checked, so collect keeps it
    while attempt exists. build creates the entry at the path it is given and
    may pass on the store lock's descriptor; roots are the entries its links
    may reach. A reproducible build gets a fixed mtime, so a rebuild at the
    same path matches the digests that other attempts recorded. The store lock
    is held until the caller is done.
    """
    store = location()
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    store = store.resolve()
    entry = store / name
    allowed = (entry, *roots)
    with open_directory(store) as directory, _locked(directory, fcntl.LOCK_EX) as lock:
        _link(directory, name + ".refs", attempt)
        digests = _verified(store, name, identity, allowed)
        if digests is None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name + ".json", dir_fd=directory)
            # pytestmut: off[stmt.call_remove] -- only a power loss tells
            os.fsync(directory)
            # pytestmut: on
            _remove(directory, name)
            build(entry, lock)
            _normalize(entry, FIXED_MTIME_NS if reproducible else None)
            digests = bootstrap.TreeDigests(
                metadata=bootstrap.tree_digest(entry, allowed, content=False),
                content=bootstrap.tree_digest(entry, allowed, content=True),
            )
            seal = {"schema": SEAL_SCHEMA, **identity, **asdict(digests)}
            write_atomic(store / (name + ".json"), canonical(seal))
        yield entry, digests


def collect() -> None:
    """Remove the entries that no attempt links to; never raise.

    A store that another process holds is left for a later activation.
    """
    try:
        store = location().resolve()
        with (
            open_directory(store) as directory,
            _locked(directory, fcntl.LOCK_EX | fcntl.LOCK_NB),
        ):
            _sweep(directory)
    except OSError, GatesealError:
        return


def linked(attempt: Path, paths: Iterable[Path]) -> bool:
    """Return whether attempt links to the store entry of each path in this store.

    A collect that could not see the attempt directory, such as one on a volume
    that was not mounted, removed its link. A path in another store, such as one
    an attempt prepared with another GATESEAL_INTERPRETER_STORE, needs none.
    """
    try:
        store = location().resolve()
    except OSError, GatesealError:
        return False
    return all(
        os.path.lexists(
            store / (path.relative_to(store).parts[0] + ".refs") / attempt.name
        )
        for path in paths
        if path.is_relative_to(store) and path != store
    )


@contextlib.contextmanager
def _locked(directory: int, operation: int) -> Generator[int]:
    """Hold the store lock and yield its descriptor."""
    descriptor = bootstrap.open_lock(LOCK, directory)
    try:
        fcntl.flock(descriptor, operation)
        yield descriptor
    finally:
        os.close(descriptor)


def _link(directory: int, refs: str, attempt: Path) -> None:
    with contextlib.suppress(FileExistsError):
        os.mkdir(refs, 0o700, dir_fd=directory)
    descriptor = os.open(refs, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        with contextlib.suppress(FileExistsError):
            os.symlink(attempt.absolute(), attempt.name, dir_fd=descriptor)
        # pytestmut: off[stmt.call_remove] -- only a power loss tells
        os.fsync(descriptor)
        # pytestmut: on
    finally:
        os.close(descriptor)


def _recorded_digests(
    document: object, identity: dict[str, object]
) -> bootstrap.TreeDigests | None:
    """Return the digests that a seal of identity records, else None."""
    if not isinstance(document, dict):
        return None
    seal = cast(dict[str, object], document)
    metadata, content = seal.get("metadata"), seal.get("content")
    if (
        set(seal) != {"schema", *identity, "metadata", "content"}
        or type(seal["schema"]) is not int
        or seal["schema"] != SEAL_SCHEMA
        or any(seal[field] != value for field, value in identity.items())
        or not isinstance(metadata, str)
        # pytestmut: off[bool.and_or] -- the digest compares refuse it too
        or not isinstance(content, str)
        # pytestmut: on
    ):
        return None
    return bootstrap.TreeDigests(metadata, content)


def _verified(
    store: Path, name: str, identity: dict[str, object], allowed: tuple[Path, ...]
) -> bootstrap.TreeDigests | None:
    """Return the entry's sealed digests if the entry still has them, else None."""
    entry = store / name
    try:
        document = bootstrap.decode_json_value(read(store, name + ".json"))
        digests = _recorded_digests(document, identity)
        if (
            digests is None
            or bootstrap.tree_digest(entry, allowed, content=False) != digests.metadata
            or bootstrap.tree_digest(entry, allowed, content=True) != digests.content
        ):
            return None
    except OSError, GatesealError, bootstrap.BootstrapError, bootstrap.JSONError:
        return None
    return digests


def _normalize(entry: Path, mtime: int | None) -> None:
    """Give every file a read-only mode and mtime, if given, parents first.

    Each folder gets its mode before os.walk lists it, so the files of a folder
    that the build left unreadable to its owner get theirs too.
    """
    os.chmod(entry, 0o555)
    for directory, directories, files in os.walk(entry):
        for name in (*directories, *files):
            _normalize_one(os.path.join(directory, name), mtime)


def _normalize_one(path: str, mtime: int | None) -> None:
    mode = os.lstat(path).st_mode
    if stat.S_ISREG(mode):
        os.chmod(path, 0o555 if mode & 0o111 else 0o444)
        if mtime is not None:
            os.utime(path, ns=(mtime, mtime))
    elif stat.S_ISDIR(mode):
        os.chmod(path, 0o555)
    elif stat.S_ISLNK(mode):
        if os.chmod in os.supports_follow_symlinks:
            # pytestmut: off[stmt.call_remove] -- Linux cannot chmod a link
            os.chmod(path, 0o755, follow_symlinks=False)
            # pytestmut: on
    else:
        raise GatesealError(
            "invalid_preparation",
            f"Activation built {path}, which is not a file, link or directory.",
            4,
        )


def _remove(directory: int, name: str) -> None:
    """Delete name in directory, read-only folders included, following no link."""
    try:
        mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    if stat.S_ISDIR(mode):
        _make_writable(directory, name)
        shutil.rmtree(name, dir_fd=directory)
    else:
        os.unlink(name, dir_fd=directory)


def _make_writable(directory: int, name: str) -> None:
    """Give folder name and every folder in it owner access, parents first.

    A folder's mode changes before the folder is opened, so a folder that lost
    its owner's read permission is emptied like any other.
    """
    os.chmod(name, 0o700, dir_fd=directory)
    # pytestmut: off[bit.and_or] -- callers lstat a real folder; only a race tells
    descriptor = os.open(name, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    # pytestmut: on
    try:
        with os.scandir(descriptor) as items:
            for item in items:
                if item.is_dir(follow_symlinks=False):
                    _make_writable(descriptor, item.name)
    finally:
        os.close(descriptor)


def _sweep(directory: int) -> None:
    names = os.listdir(directory)
    for key in sorted({found[1] for found in map(NAME.fullmatch, names) if found}):
        if _prune(directory, key + ".refs"):
            continue
        with contextlib.suppress(FileNotFoundError):
            os.unlink(key + ".json", dir_fd=directory)
        # pytestmut: off[stmt.call_remove] -- only a power loss tells
        os.fsync(directory)
        # pytestmut: on
        _remove(directory, key)
        _remove(directory, key + ".refs")
    for name in names:
        if name.startswith(".gateseal-"):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(name, dir_fd=directory)


def _prune(directory: int, refs: str) -> int:
    """Unlink refs' dead links and junk; return how many live links remain."""
    try:
        mode = os.stat(refs, dir_fd=directory, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return 0  # pytestmut: skip[return.none] -- the caller tests its truth
    if not stat.S_ISDIR(mode):
        return 0  # pytestmut: skip[return.none] -- the caller tests its truth
    # pytestmut: off[bit.and_or] -- the lstat above found a folder; only a race tells
    descriptor = os.open(refs, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    # pytestmut: on
    live = 0
    try:
        for item in os.scandir(descriptor):
            if not (bootstrap.ATTEMPT.fullmatch(item.name) and item.is_symlink()):
                _remove(descriptor, item.name)
            elif _target_is_directory(descriptor, item.name):
                live += 1  # pytestmut: skip[aug.swap, num.shift] -- only zero matters
            else:
                os.unlink(item.name, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    return live


def _target_is_directory(directory: int, name: str) -> bool:
    """Return whether link name reaches a directory; keep it when unsure."""
    try:
        return stat.S_ISDIR(os.stat(name, dir_fd=directory).st_mode)
    except FileNotFoundError, NotADirectoryError:
        return False  # pytestmut: skip[return.none] -- the caller tests its truth
    except OSError:
        return True
