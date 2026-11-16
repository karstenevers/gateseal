"""What an in-process test needs in place of downloads and installed tools.

configure writes a clone's tracked policy without a compiler download, and
executable writes the shell script that stands in for a program.
"""

from gateseal import __version__, lock

SOURCE = (
    "https://raw.githubusercontent.com/example/wheels/"
    f"{'0' * 40}/artifacts/gateseal-{__version__}-py3-none-any.whl"
)


def configure(worktree, capabilities):
    """Write the tracked policy for capabilities, with a compiler not yet fetched."""
    lock.configure(
        worktree.root,
        worktree.git,
        worktree.preparation,
        list(capabilities),
        lock.PolicyRequest(),
        None,
        SOURCE,
        "a" * 64,
        None,
    )


def executable(path, text="#!/bin/sh\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path
