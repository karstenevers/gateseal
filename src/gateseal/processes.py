"""Start the exact executables gateseal prepares or requires, without a shell."""

import re
import shutil
import subprocess
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.environments import clean_environment

MINIMUM_GIT = "2.54.0"
"""The oldest Git that gateseal accepts. Git 2.54.0 added `git hook list`, which
gateseal runs to read the hooks that Git's configuration defines."""


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
        raise GatesealError(
            "tool_unavailable", "A required exact executable could not start.", 4
        ) from exc
    if result.returncode and not allow_failure:
        detail = (result.stdout + result.stderr).decode("utf-8", errors="replace")
        raise GatesealError("tool_failed", detail, 4, next_step)
    return result


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
