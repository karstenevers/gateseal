"""The compiler release a Lock pins.

Its repository, version and commit, and the uv and pre-commit it works with.
"""

import re
from dataclasses import dataclass
from typing import Self

from gateseal import __version__
from gateseal.diagnostics import GatesealError
from gateseal.validation import (
    fields,
    string,
    version,
)

REPOSITORY = "https://github.com/karstenevers/gateseal"
"""gateseal's repository, whose release tags a Lock pins by commit."""

COMMIT = re.compile(r"[0-9a-f]{40}")

CHECK_HOOK = "gateseal-check"
"""The hook that gateseal's repository defines in .pre-commit-hooks.yaml."""

CHECK_HOOK_NAME = "Check files and tools against gateseal.lock"
"""The name that pre-commit prints for CHECK_HOOK."""

MINIMUM_UV = "0.13.5"
"""The oldest uv that gateseal setup accepts."""

PRE_COMMIT = "4.7.0"
"""The pre-commit this release runs: gateseal setup installs it, and the
generated configuration names it as the oldest pre-commit to run."""


@dataclass(frozen=True)
class GatesealPin:
    """The gateseal release a Lock pins: its repository, version and commit.

    pre-commit builds gateseal's hook from the commit and CI installs gateseal
    from it, so the commit, not a wheel, names the code that checks a commit.
    """

    repository: str
    version: str
    commit: str

    def document(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "version": self.version,
            "commit": self.commit,
        }

    @classmethod
    def parse(cls, value: object) -> Self:
        item = fields(value, {"repository", "version", "commit"}, "gateseal")
        commit = string(item["commit"], "gateseal.commit")
        if not COMMIT.fullmatch(commit):
            raise GatesealError(
                "invalid_field", "gateseal.commit must be a full SHA-1 commit."
            )
        return cls(
            string(item["repository"], "gateseal.repository"),
            version(item["version"], "gateseal.version"),
            commit,
        )

    @property
    def running(self) -> bool:
        """Tell whether this is the gateseal that runs, from its repository."""
        return (self.repository, self.version) == (REPOSITORY, __version__)

    def hook_repository(self) -> dict[str, object]:
        """Return the native configuration's repository of the check hook."""
        return {
            "repo": self.repository,
            "rev": self.commit,
            "hooks": [{"id": CHECK_HOOK}],
        }
