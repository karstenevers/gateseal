"""Start the exact executables gateseal prepares or requires, without a shell."""

import re
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from io import BufferedReader
from pathlib import Path
from selectors import EVENT_READ, DefaultSelector
from time import monotonic
from typing import cast

from gateseal.diagnostics import GatesealError
from gateseal.environments import clean_environment

MINIMUM_GIT = "2.54.0"
"""The oldest Git that gateseal accepts. Git 2.54.0 added `git hook list`, which
gateseal runs to read the hooks that Git's configuration defines."""


@dataclass(frozen=True)
class Timed:
    """A finished process, the seconds it ran, and its timed lines of output.

    lines holds each line of standard output that ends in a line feed, without
    the line feed, and the seconds from the arrival of its first byte to the
    arrival of its line feed.
    """

    process: subprocess.CompletedProcess[bytes]
    seconds: float
    lines: tuple[tuple[bytes, float], ...]


def execute(
    arguments: list[str],
    root: Path,
    env: dict[str, str],
    *,
    allow_failure: bool = False,
    next_step: str = "",
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            arguments, cwd=root, env=env, capture_output=True, check=False
        )
    except OSError as exc:
        raise _unavailable() from exc
    if result.returncode and not allow_failure:
        detail = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        raise GatesealError("tool_failed", detail, 4, next_step)
    return result


def execute_timed(arguments: list[str], root: Path, env: dict[str, str]) -> Timed:
    """Run arguments as execute does with allow_failure, and time its output.

    One selector reads both pipes, so a process that fills its standard error
    cannot stop while standard output is read, and an interrupt waits for no
    process that kept a pipe open.
    """
    started = monotonic()
    try:
        child = subprocess.Popen(
            arguments, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
    except OSError as exc:
        raise _unavailable() from exc
    chunks: list[tuple[float, bytes]] = []
    errors: list[tuple[float, bytes]] = []
    with child, DefaultSelector() as selector:
        selector.register(cast(BufferedReader, child.stdout), EVENT_READ, chunks)
        selector.register(cast(BufferedReader, child.stderr), EVENT_READ, errors)
        try:
            while selector.get_map():
                for key, _ in selector.select():
                    if chunk := cast(BufferedReader, key.fileobj).read1():
                        cast(list[tuple[float, bytes]], key.data).append(
                            (monotonic(), chunk)
                        )
                    else:
                        selector.unregister(key.fileobj)
        except BaseException:
            child.kill()
            raise
    seconds = monotonic() - started
    output, stderr = (b"".join(c for _, c in parts) for parts in (chunks, errors))
    process = subprocess.CompletedProcess(arguments, child.returncode, output, stderr)
    return Timed(process, seconds, timed_lines(chunks))


def timed_lines(
    chunks: Iterable[tuple[float, bytes]],
) -> tuple[tuple[bytes, float], ...]:
    """Return each line of the chunks that ends in a line feed, with its seconds.

    Each chunk comes with the instant it arrived. A line's seconds run from
    the arrival of its first byte to the arrival of its line feed. A last
    piece without a line feed is no line. The pieces of a line are joined once
    it ends, so a long line costs no copy per chunk.
    """
    lines: list[tuple[bytes, float]] = []
    pending: list[tuple[float, bytes]] = []
    for arrival, chunk in chunks:
        *ended, rest = chunk.split(b"\n")
        for piece in ended:
            began = pending[0][0] if pending else arrival
            line = b"".join([*(part for _, part in pending), piece])
            lines.append((line, arrival - began))
            pending = []
        if rest:
            pending.append((arrival, rest))
    return tuple(lines)


def _unavailable() -> GatesealError:
    return GatesealError(
        "tool_unavailable", "A required exact executable could not start.", 4
    )


def find_tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise GatesealError(
            "missing_prerequisite", f"Install the documented {name} prerequisite.", 4
        )
    return str(Path(found).absolute())


def find_git(root: Path) -> str:
    """Return find_tool's git once it reports MINIMUM_GIT or later in root.

    git answers in root, because a shim such as mise's picks its Git by folder.
    """
    git = find_tool("git")
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
        return git
    version = "Git " + ".".join(map(str, release)) if release else "no Git version"
    raise GatesealError(
        "git_version_mismatch",
        f"{git} reports {version}, and gateseal requires Git {MINIMUM_GIT} or later.",
        4,
        f"Install Git {MINIMUM_GIT} or later, then retry.",
    )


def pre_commit_command(python: str, arguments: list[str]) -> list[str]:
    return [python, "-I", "-B", "-m", "pre_commit", *arguments]
