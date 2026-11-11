import contextlib
import os
import stat
import uuid
from collections.abc import Generator
from pathlib import Path

from gateseal.diagnostics import GatesealError


def relative_name(name: str) -> str:
    if not name or "\\" in name or ":" in name or "\x00" in name:
        raise GatesealError("unsafe_path", "Expected a contained relative path.")
    if any(part in ("", ".", "..") for part in name.split("/")) or name.startswith("/"):
        raise GatesealError("unsafe_path", f"Unsafe relative path {name!r}.")
    return name


@contextlib.contextmanager
def open_directory(path: Path) -> Generator[int]:
    absolute = path.absolute()
    # pytestmut: off[bit.and_or] -- the anchor / is a directory
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    # pytestmut: on
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = next_fd
    except FileNotFoundError:
        os.close(fd)
        raise
    except OSError as exc:
        os.close(fd)
        raise GatesealError(
            "unsafe_path", f"Cannot open ordinary directory {path}."
        ) from exc
    try:
        yield fd
    finally:
        os.close(fd)


def read(root: Path, name: str, *, non_executable: bool = False) -> bytes:
    """Read a regular file under root without following symlinks.

    O_NONBLOCK lets a FIFO be opened and rejected instead of hanging the open.
    """
    relative_name(name)
    with open_directory(root / Path(name).parent) as fd:
        try:
            source = os.open(
                Path(name).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            with os.fdopen(source, "rb") as handle:
                before = os.fstat(handle.fileno())
                if not stat.S_ISREG(before.st_mode):
                    raise GatesealError("unsafe_path", f"{name} is not a regular file.")
                data = handle.read()
                after = os.fstat(handle.fileno())
                # pytestmut: off[bool.literal] -- only a race swaps in a link
                now = os.stat(Path(name).name, dir_fd=fd, follow_symlinks=False)
                # pytestmut: on

                def identity(s: os.stat_result) -> tuple[int, int, int, int, int]:
                    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

                # pytestmut: off[bool.and_or] -- only a race changes one stat
                if identity(before) != identity(after) or identity(after) != identity(
                    now
                ):
                    # pytestmut: on
                    raise GatesealError(
                        "concurrent_change", f"{name} changed during reading."
                    )
                observed = stat.S_IMODE(after.st_mode)
                if non_executable and observed & 0o111:
                    raise GatesealError(
                        "file_mode_mismatch",
                        f"{name} has mode {observed:04o}; "
                        "restore non-executable permissions.",
                    )
                return data
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise GatesealError(
                "unsafe_path", f"Cannot read regular file {name}."
            ) from exc


def read_optional(
    root: Path, name: str, *, non_executable: bool = False
) -> bytes | None:
    try:
        return read(root, name, non_executable=non_executable)
    except FileNotFoundError:
        return None


def make_parents(root: Path, name: str) -> None:
    """Create each missing directory of name under root, following no link.

    Each component is opened as an ordinary directory before the next is made
    in it, so a symlinked ancestor fails as unsafe_path.
    """
    parts = Path(relative_name(name)).parts[:-1]
    for depth, part in enumerate(parts):
        with (
            open_directory(root.joinpath(*parts[:depth])) as directory,
            contextlib.suppress(FileExistsError),
        ):
            os.mkdir(part, 0o755, dir_fd=directory)


def remove(root: Path, name: str) -> None:
    """Delete the file name under root, following no link; a missing one is fine."""
    with (
        open_directory(root / Path(relative_name(name)).parent) as parent,
        contextlib.suppress(FileNotFoundError),
    ):
        os.unlink(Path(name).name, dir_fd=parent)


def write_atomic(path: Path, content: bytes, mode: int = 0o600) -> None:
    with open_directory(path.parent) as parent:
        temporary = ".gateseal-" + uuid.uuid4().hex
        # pytestmut: off[bit.and_or] -- the uuid4 name is always new
        exclusive = os.O_EXCL | os.O_NOFOLLOW
        # pytestmut: on
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | exclusive, mode, dir_fd=parent
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fchmod(handle.fileno(), mode)
                # pytestmut: off[stmt.call_remove] -- only a power loss tells
                os.fsync(handle.fileno())
                # pytestmut: on
            os.replace(
                Path(temporary).name, path.name, src_dir_fd=parent, dst_dir_fd=parent
            )
            # pytestmut: off[stmt.call_remove] -- only a power loss tells
            os.fsync(parent)
            # pytestmut: on
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(Path(temporary).name, dir_fd=parent)
