"""What an in-process test needs in place of downloads and installed tools.

configure writes a clone's tracked policy with conftest's PIN for gateseal,
and executable writes the shell script that stands in for a program.
"""

from gateseal import lock


def configure(worktree, capabilities):
    """Write the tracked policy for capabilities with the bundled policy."""
    lock.configure(
        worktree.root,
        worktree.git,
        worktree.preparation,
        list(capabilities),
        lock.PolicyRequest(),
        None,
    )


def executable(path, text="#!/bin/sh\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path
