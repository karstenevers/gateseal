"""The machines gateseal supports, named as each tool names them."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Target:
    """One supported machine, named as each tool that acquires for it names it.

    platform is the target triple of python-build-standalone and of uv's
    --python-platform.
    """

    python: str
    mise: str
    platform: str


TARGETS = {
    ("Darwin", "arm64"): Target(
        "darwin-aarch64-none", "macos-arm64", "aarch64-apple-darwin"
    ),
    ("Linux", "x86_64"): Target(
        "linux-x86_64-gnu", "linux-x64", "x86_64-unknown-linux-gnu"
    ),
}
"""The machines a compiler release carries a CPython for."""
