"""Name the step that recovers from a failed download, by the cause uv reports.

uv's output says why a download failed: the request got no response, as on a
machine without network; the host refused it, as a private repository answers
a request without credentials; or the file does not match its pinned SHA-256.
Each cause has its own recovery, so one fixed step would fit only one of them.
"""

import contextlib
import re
from collections.abc import Generator, Mapping
from urllib.parse import urlsplit

from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError

UNREACHABLE = re.compile(r"error sending request for url")
REFUSED = re.compile(r"HTTP status client error \((?:401|404) ")
HASH_MISMATCH = re.compile(r"Hash mismatch")


def python_download(url: str, cache: str) -> contextlib.AbstractContextManager[None]:
    """Name the recovery when uv cannot install the CPython archive at url.

    cache is the directory of uv's cached Python archives.
    """
    return _recovering(
        {
            UNREACHABLE: f"Give this machine network access to {url}, "
            "then run gateseal activate.",
            HASH_MISMATCH: "uv's cached Python archive does not match its pinned "
            f"SHA-256. Delete {cache}, then run gateseal activate.",
        }
    )


def compiler_download(
    artifact: CompilerArtifact,
) -> contextlib.AbstractContextManager[None]:
    """Name the recovery when uv cannot download the Lock's compiler wheel.

    A copy of the wheel saved on this machine needs no download, so every
    recovery offers it.
    """
    url = artifact.location
    local = (
        f"Or save {url} as {artifact.filename} and run gateseal activate "
        "--compiler-wheel with its absolute path."
    )
    return _recovering(
        {
            UNREACHABLE: f"Give this machine network access to {url}, then run "
            f"gateseal activate. {local}",
            REFUSED: "Set NETRC to a mode-0600 file that holds machine "
            f"{urlsplit(url).hostname} login __token__ password TOKEN, with a "
            f"TOKEN that can read {url}, then run gateseal activate. {local}",
        }
    )


@contextlib.contextmanager
def _recovering(steps: Mapping[re.Pattern[str], str]) -> Generator[None]:
    """Give a failure raised in the block the step whose cause its output shows.

    A failure that shows none of the causes keeps the step it was raised with.
    """
    try:
        yield
    except GatesealError as exc:
        step = next((s for cause, s in steps.items() if cause.search(str(exc))), None)
        if step is None:
            raise
        raise GatesealError(exc.code, str(exc), exc.exit_code, step) from exc
