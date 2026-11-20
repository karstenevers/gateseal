"""The gateseal-node-packages hook: install a Node project's packages.

A tool of a Node project, such as tsc, reads the packages in the project's
node_modules, so this hook installs what the lockfile pins before the tool
hooks run. No install script runs, because a package's script is code that no
one reviewed. npm installs in two steps: the dry run of npm ci proves that
package-lock.json matches package.json, and npm install --no-save then installs
only what changed. pnpm's frozen install does both in one step.

The hook picks the package manager when it runs, from the lockfile that the
repository commits, because the generated configuration must be the same for an
npm and a pnpm project. So gateseal configure gives the hook the command that
starts each package manager on the pinned Node, one argument each, such as
npm=<command>. A package manager without an argument starts from PATH by its
name. pre-commit builds the hook from gateseal's repository at the commit the
Lock pins.
"""

import os
import shlex
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.processes import find_tool
from gateseal.tools.package_managers import package_manager

NEEDS = "A selected check runs in the repository's Node project, but"


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
                next_step="Run gateseal configure, which writes the hook's arguments. "
                "Then push again.",
            )
        found[name] = words
    return found


def install(root: Path, starts: Mapping[str, Sequence[str]]) -> None:
    """Install the packages that the lockfile at root pins.

    starts holds the words that start a package manager's own executable, by
    its name. Each command's output passes through, so pre-commit shows it when
    the hook fails.
    """
    manager = package_manager(root, NEEDS)
    start = [*starts.get(manager.name, [manager.name])]
    start[0] = find_tool(start[0])
    env = os.environ | dict(manager.environment)
    for arguments in manager.install:
        if manager.record is not None:
            (root / manager.record).unlink(missing_ok=True)
        if subprocess.run(
            [*start, *arguments], cwd=root, env=env, check=False
        ).returncode:
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
