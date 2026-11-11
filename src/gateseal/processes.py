"""Start the exact executables gateseal prepares or requires, without a shell."""

import shutil
import subprocess
from pathlib import Path

from gateseal.bootstrap import MINIMUM_GIT, BootstrapError, require_git
from gateseal.diagnostics import GatesealError


def execute(
    arguments: list[str],
    root: Path,
    env: dict[str, str],
    *,
    allow_failure: bool = False,
    next_step: str = "gateseal activate",
    stdin: bytes | None = None,
    inherit: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            arguments,
            cwd=root,
            env=env,
            input=stdin,
            capture_output=True,
            check=False,
            pass_fds=inherit,
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
    """Return find_tool's git once it reports MINIMUM_GIT or later in root."""
    git = find_tool("git")
    try:
        require_git(Path(git), root)
    except BootstrapError as exc:
        raise GatesealError(
            "git_version_mismatch",
            str(exc),
            4,
            f"Install Git {MINIMUM_GIT} or later, then retry.",
        ) from exc
    return git


def find_python(uv: str, root: Path, env: dict[str, str], request: str) -> str:
    result = execute(
        [uv, "python", "find", "--no-config", "--no-python-downloads", request],
        root,
        env,
    )
    path = Path(result.stdout.decode().strip())
    if not path.is_absolute() or not path.is_file():
        raise GatesealError(
            "invalid_preparation", "uv did not return an absolute Python executable.", 4
        )
    return str(path)


def pre_commit_command(python: str, arguments: list[str]) -> list[str]:
    return [python, "-I", "-B", "-m", "pre_commit", *arguments]
