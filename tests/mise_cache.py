"""Every mise in the tests runs with the network off, so a failed download
cannot fail a test that checks something else. The session first fills one
store with the downloads of gateseal fetch, and CI keeps that store between
runs.
"""

import fcntl
import os
from pathlib import Path

from gateseal import fetch
from gateseal.diagnostics import GatesealError

STORE = "mise-downloads"

LINK = "downloads-cas"
"""mise's own folder of downloaded archives. Only it links to the shared store,
because mise exec also leaves a copy of each archive in the cache folder."""


def link(config, folder):
    cache = getattr(config, "cache", None)
    store = cache.mkdir(STORE) if cache else folder.with_name(STORE)
    store.mkdir(exist_ok=True)
    (folder / LINK).symlink_to(store, target_is_directory=True)
    return {"MISE_CACHE_DIR": str(folder), "MISE_OFFLINE": "1"}


def fill(tools, scratch):
    """mise downloads nothing for a version it finds installed, so it sees only
    the installs in scratch."""
    dropped = ("MISE_OFFLINE", "MISE_INSTALLS_DIR")
    env = {name: value for name, value in os.environ.items() if name not in dropped}
    env |= {
        "MISE_DATA_DIR": str(scratch / "data"),
        "MISE_STATE_DIR": str(scratch / "state"),
        "MISE_SYSTEM_DATA_DIR": str(scratch / "system"),
    }
    store = (Path(env["MISE_CACHE_DIR"]) / LINK).resolve()
    with open(store.with_name(store.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            fetch.download(fetch.release_binaries(tools), scratch, env)
        except GatesealError as failed:
            failed.add_note(failed.details)
            raise
