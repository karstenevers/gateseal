"""The gateseal-check hook: check what a commit records against its Lock.

pre-commit builds the hook from gateseal's repository at the commit that the
Lock pins and runs it at git commit, before the tools' own hooks. It reads the
index that the commit records, which Git names in GIT_INDEX_FILE for git
commit -a or a commit of paths. The staged Manifest and each owned file that
the staged Lock lists must have the SHA-256 that Lock records. So must each
installed executable that a tool checks at commit, because mise never checks
an install again. When the staged Lock records a tool that runs in the uv
project, the commit must hold uv.lock as a regular TOML file, and it must
not install the tool's package from the project's own source. Only the staged
files are read, so the hook needs no policy and no network. When the hook
fails, pre-commit runs no later hook, so no tool runs the files that the hook
refused. gateseal check and the CI gate also compile the policy again, which
catches an edited Lock.
"""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from gateseal import lock
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import LOCK, MANIFEST, tracked_bytes
from gateseal.platforms import machine
from gateseal.processes import find_tool
from gateseal.tools.release_binary import check_installs
from gateseal.tools.uv_project import PROJECT_FILES, refuse_shadowing
from gateseal.validation import parse_toml, sha

STAGE_ALL = f"Stage {MANIFEST}, {LOCK} and the files gateseal wrote, then commit again."


def check(staged: Callable[[str], bytes | None], root: Path) -> None:
    """Fail unless the staged files and the checked installs are the recorded ones."""
    ledger = lock.own_ledger(_required(staged, LOCK), machine())
    for name, recorded in ledger.files.items():
        if sha(_required(staged, name)) == recorded:
            continue
        if name == MANIFEST:
            raise GatesealError(
                "lock_mismatch",
                f"The staged {MANIFEST} is not the one that {LOCK} was compiled from.",
                next_step=f"Run gateseal configure. {STAGE_ALL}",
            )
        raise GatesealError(
            "ownership_conflict",
            f"The staged {name} differs from the file gateseal wrote.",
            next_step=f"Undo the edit to {name}, or change {MANIFEST} and run "
            f"gateseal configure. {STAGE_ALL}",
        )
    check_installs(ledger.programs, root)
    if ledger.packages:
        uv_lock = staged(PROJECT_FILES[1])
        if uv_lock is None:
            raise GatesealError(
                "missing_project",
                f"This commit has no {PROJECT_FILES[1]} as a regular file, which uv "
                f"reads to run {', '.join(ledger.packages)}.",
                next_step=f"Run uv lock if the project has none. Stage "
                f"{PROJECT_FILES[1]} as a regular file, then commit again.",
            )
        try:
            parse_toml(uv_lock)
        except GatesealError as exc:
            raise GatesealError(
                exc.code,
                f"This commit's {PROJECT_FILES[1]} is not TOML, so gateseal cannot "
                f"check where it installs {', '.join(ledger.packages)} from.",
                next_step=f"Run uv lock. Stage {PROJECT_FILES[1]}, then commit again.",
            ) from exc
        refuse_shadowing(ledger.packages, uv_lock)


def _required(staged: Callable[[str], bytes | None], name: str) -> bytes:
    data = staged(name)
    if data is None:
        raise GatesealError(
            "missing_configuration",
            f"This commit has no {name}.",
            next_step=f"Run gateseal configure. {STAGE_ALL}",
        )
    return data


def index(root: Path, git: str) -> Callable[[str], bytes | None]:
    """Return a reader of each file's staged bytes, as Git compares them.

    It returns None for a file that the commit does not hold as a regular
    file. Git stages a link as its target's name, which a reader would
    otherwise take for the file that the link names.
    """

    def staged(name: str) -> bytes | None:
        listed = subprocess.run(
            [git, "--literal-pathspecs", "ls-files", "--stage", "-z", "--", name],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if not listed.stdout.startswith((b"100644 ", b"100755 ")):
            return None
        result = subprocess.run(
            [git, "cat-file", "blob", f":{name}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        return None if result.returncode else tracked_bytes(result.stdout)

    return staged


def main() -> int:
    root = Path.cwd()
    try:
        check(index(root, find_tool("git")), root)
    except GatesealError as exc:
        print(f"gateseal: {exc}\nNext: {exc.next_step}", file=sys.stderr)
        return 1
    return 0
