"""The gateseal-node-packages hook: install a Node project's packages.

A tool of a Node project, such as tsc, reads the packages in the project's
node_modules, so this hook installs what the lockfile pins before the tool
hooks run. No install script runs, because a package's script is code that no
one reviewed. npm checks before it installs: the dry run of npm ci proves that
package-lock.json matches the versions in package.json, and npm ls
--package-lock-only that it matches their sources. npm install --no-save then
installs only what changed. pnpm's frozen install does both in one step. A
setting of the project or the user can make the hook fail, but cannot make it
pass with other packages than the lockfile pins.

The hook picks the package manager when it runs, from the lockfile that the
repository commits, because the generated configuration must be the same for an
npm and a pnpm project. So gateseal configure gives the hook the command that
starts each package manager on the pinned Node, one argument each, such as
npm=<command>. pre-commit builds the hook from gateseal's repository at the
commit the Lock pins.
"""

import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.processes import find_tool
from gateseal.tools.package_managers import PackageManager, package_manager

NEEDS = "A selected check runs in the repository's Node project, but"
RECONFIGURE = (
    "Run gateseal configure, which writes the hook's arguments. Then push again."
)


def start_commands(arguments: Sequence[str]) -> dict[str, list[str]]:
    """Return the words that start each package manager, from the hook's arguments.

    Each argument is name=command, and command is a shell-quoted command line.
    """
    found: dict[str, list[str]] = {}
    for argument in arguments:
        name, _, command = argument.partition("=")
        try:
            words = shlex.split(command)
        except ValueError:
            words = []
        if not words:
            raise GatesealError(
                "invalid_invocation",
                "Each argument of gateseal-node-packages must be a package "
                f"manager's name, =, and the command that starts it, not {argument!r}.",
                next_step=RECONFIGURE,
            )
        found[name] = words
    return found


def install(root: Path, starts: Mapping[str, Sequence[str]]) -> None:
    """Install the packages that the lockfile at root pins.

    starts holds the words that start a package manager's own executable, by
    its name. Each command's output passes through, so pre-commit shows it when
    the hook fails. Each check leaves the package manager's record as it found
    it, also when the check fails or the hook is stopped.
    """
    manager = package_manager(root, NEEDS)
    if manager.name not in starts:
        raise GatesealError(
            "invalid_invocation",
            f"gateseal-node-packages has no argument that starts {manager.name}.",
            next_step=RECONFIGURE,
        )
    start = [*starts[manager.name]]
    start[0] = find_tool(start[0])
    env = os.environ | dict(manager.environment)
    record = None if manager.record is None else root / manager.record
    kept = None if record is None else _read(record)
    for check in manager.checks:
        try:
            _run(manager, [*start, *check], root, env)
        finally:
            if record is not None:
                _put_back(record, kept)
    _run(manager, [*start, *manager.install], root, env)


Kept = tuple[bytes, int, int]
"""A file's content and its access and modification times in nanoseconds."""


def _read(path: Path) -> Kept | None:
    """Return what path holds, or None when it does not exist."""
    try:
        status = path.stat()
        return path.read_bytes(), status.st_atime_ns, status.st_mtime_ns
    except FileNotFoundError:
        return None


def _put_back(path: Path, kept: Kept | None) -> None:
    """Make path hold kept again, with its times, or delete path for None.

    npm trusts its record only when no folder of node_modules is newer, so the
    record keeps its old time and npm still ignores it after a later change.
    """
    if kept is None:
        path.unlink(missing_ok=True)
        return
    content, accessed, modified = kept
    path.write_bytes(content)
    os.utime(path, ns=(accessed, modified))


def _run(
    manager: PackageManager, command: list[str], root: Path, env: Mapping[str, str]
) -> None:
    """Run command in root, or stop when it fails."""
    if subprocess.run(command, cwd=root, env=env, check=False).returncode:
        raise GatesealError(
            "tool_failed",
            f"{manager.name} could not install the packages that "
            f"{manager.lockfile} pins.",
            next_step=f"If {manager.lockfile} does not match package.json, run "
            f"{manager.name} install and commit {manager.lockfile}. Otherwise "
            f"read the output of {manager.name} above. Then push again.",
        )


def main() -> int:
    try:
        install(Path.cwd(), start_commands(sys.argv[1:]))
    except GatesealError as exc:
        print(f"gateseal: {exc}\nNext: {exc.next_step}", file=sys.stderr)
        return 1
    return 0
