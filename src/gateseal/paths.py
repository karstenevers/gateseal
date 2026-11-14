"""Resolve paths the same way on every supported Python."""

import os
from pathlib import Path


def resolved(path: Path | str, *, strict: bool = False) -> Path:
    """Resolve path as Path.resolve does since Python 3.13.

    Before 3.13, Path.resolve raises RuntimeError on a symlink loop. A strict
    resolve raises OSError for it here, and a lenient one returns a path.
    """
    return Path(os.path.realpath(path, strict=strict))
