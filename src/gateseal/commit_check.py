"""The gateseal-check hook: check what a commit records against its Lock.

pre-commit builds the hook from gateseal's repository at the commit that the
Lock pins and runs it at git commit, before the tools' own hooks. It reads the
index that the commit records, which Git names in GIT_INDEX_FILE for git
commit -a or a commit of paths. The staged Manifest and each owned file that
the staged Lock lists must have the SHA-256 that Lock records. So must each
installed executable that a tool checks at commit, because mise never checks
an install again. Only the staged Lock is read, so the hook needs no policy
and no network. gateseal check and the CI gate also compile the policy again,
which catches an edited Lock.
"""

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from gateseal import lock
from gateseal.bootstrap import LOCK, MANIFEST, sha, tracked_bytes
from gateseal.diagnostics import GatesealError
from gateseal.platforms import machine
from gateseal.processes import find_tool
from gateseal.tools.release_binary import check_installs

STAGE_ALL = f"Stage {MANIFEST}, {LOCK} and the files gateseal wrote, then commit again."


def check(staged: Callable[[str], bytes], root: Path) -> None:
    """Fail unless the staged files and the checked installs are the recorded ones."""
    target = machine()
    ledger = lock.own_ledger(staged(LOCK), None if target is None else target.mise)
    for name, recorded in ledger.files.items():
        if sha(staged(name)) == recorded:
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


def index(root: Path, git: str) -> Callable[[str], bytes]:
    """Return a reader of each file's staged bytes, as Git compares them."""

    def staged(name: str) -> bytes:
        result = subprocess.run(
            [git, "cat-file", "blob", f":{name}"],
            cwd=root,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise GatesealError(
                "missing_configuration",
                f"This commit has no {name}.",
                next_step=f"Run gateseal configure. {STAGE_ALL}",
            )
        return tracked_bytes(result.stdout)

    return staged


def main() -> int:
    root = Path.cwd()
    try:
        check(index(root, find_tool("git")), root)
    except GatesealError as exc:
        print(f"gateseal: {exc}\nNext: {exc.next_step}", file=sys.stderr)
        return 1
    return 0
