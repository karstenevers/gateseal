"""The machines gateseal supports, named as mise names them."""

import platform

TARGETS = {("Darwin", "arm64"): "macos-arm64", ("Linux", "x86_64"): "linux-x64"}
"""mise's name for each supported system and architecture."""


def machine() -> str | None:
    """Return mise's name for this machine, or None if gateseal does not support it."""
    return TARGETS.get((platform.system(), platform.machine()))
