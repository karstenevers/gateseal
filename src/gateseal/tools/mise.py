"""What gateseal asks of mise, for every kind whose hooks start through it.

A hook names each platform's archive by URL and SHA-256 in mise's request,
and mise installs it at its first use. gateseal policy lock reads each
SHA-256 from the release's checksum file and checks it against the
downloaded archive. Kind modules and the shared node module import this
module, so no kind imports another to start through mise.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from gateseal.diagnostics import GatesealError
from gateseal.npm_tool import mise_names
from gateseal.processes import execute, find_tool
from gateseal.tools.model import MissingUpstream, Upstream
from gateseal.validation import version_key

MISE = ("mise", "--no-config", "exec")
"""Start a program from a tool that the command names, and from no mise file."""

MINIMUM_MISE = "2026.10.19"
"""The oldest mise that installs an archive as a hook's entry asks it to."""


class Download(Protocol):
    """An archive as mise's request names it: its URL and its SHA-256."""

    @property
    def url(self) -> str: ...
    @property
    def sha256(self) -> str: ...


def mise_request(name: str, version: str, platforms: Mapping[str, Download]) -> str:
    """Return mise's request for a tool, with every platform's archive.

    gateseal's hook that starts a tool from npm builds the same request from
    the Lock, so npm_tool.mise_names owns its form.
    """
    return mise_names(name, version, archives(platforms))[0]


def archives(platforms: Mapping[str, Download]) -> dict[str, tuple[str, str]]:
    """Return each platform's URL and SHA-256, as npm_tool takes them."""
    return {
        platform: (archive.url, archive.sha256)
        for platform, archive in platforms.items()
    }


def read_checksums(listing: bytes) -> dict[str, str]:
    """Return each file's SHA-256 in a checksum file as sha256sum writes it."""
    listed: dict[str, str] = {}
    for line in listing.decode("utf-8", "replace").splitlines():
        checksum, _, name = line.partition(" ")
        name = name.lstrip(" *")
        if re.fullmatch("[0-9a-f]{64}", checksum) and name:
            listed[name] = checksum
    return listed


def check_archive(
    upstream: Upstream, url: str, sha256: str, name: str, version: str, source: str
) -> bytes:
    """Download an archive and return it, once it has the SHA-256 that source lists."""
    data = upstream.download(url)
    if data is None:
        raise MissingUpstream(
            "upstream_asset_missing",
            f"The {name} {version} release has no {url}.",
            name,
            version,
        )
    if hashlib.sha256(data).hexdigest() != sha256:
        raise GatesealError(
            "upstream_checksum_mismatch",
            f"{url} does not have the SHA-256 {sha256} that {source} lists.",
            next_step=f"Do not review {name} {version} until its "
            "maintainers publish an archive that matches its checksum file.",
        )
    return data


def require_mise(env: dict[str, str]) -> str:
    """Return the mise on PATH once it reports MINIMUM_MISE or later."""
    mise = find_tool("mise")
    # mise writes its own cache and state even to report its version, so they
    # go to a scratch directory rather than the user's home.
    with tempfile.TemporaryDirectory() as scratch:
        reported = execute(
            [mise, "--no-config", "version"],
            Path(scratch),
            dict(_mise_environment(Path(scratch), env), MISE_OFFLINE="1"),
        )
    try:
        supported = version_key(reported.stdout.decode().split()[0]) >= version_key(
            MINIMUM_MISE
        )
    except (IndexError, GatesealError):
        supported = False
    if not supported:
        raise GatesealError(
            "mise_version_mismatch",
            f"gateseal requires mise {MINIMUM_MISE} or later.",
            4,
        )
    return mise


_FOUND = {"missing_prerequisite": "none is", "mise_version_mismatch": "an older one is"}
"""What the PATH holds for each way require_mise finds no usable mise."""


def mise_problem(need: str, error: GatesealError) -> str:
    """Complete need with what require_mise found, or report mise's own failure."""
    found = _FOUND.get(error.code)
    if found is None:
        return f"The mise on PATH did not report its version: {str(error).strip()}"
    return f"{need} mise {MINIMUM_MISE} or later, and {found} on PATH."


def _mise_environment(directory: Path, env: dict[str, str]) -> dict[str, str]:
    """Return an environment in which mise reads no caller settings or installs.

    mise --no-config reads no configuration file, and env holds no MISE_
    variable, so only the shared download cache is the caller's. mise checks
    each archive against the Lock's checksum, so a cached archive is safe to
    reuse. Without MISE_SYSTEM_DATA_DIR, mise treats a system install as
    installed.
    """
    env = dict(env)
    if "MISE_CACHE_DIR" in os.environ:
        env["MISE_CACHE_DIR"] = os.environ["MISE_CACHE_DIR"]
    env.update(
        {
            "MISE_DATA_DIR": str(directory / "data"),
            "MISE_STATE_DIR": str(directory / "state"),
            "MISE_SYSTEM_DATA_DIR": str(directory / "system"),
        }
    )
    return env
