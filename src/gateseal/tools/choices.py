"""The Policy Choices a Tool Definition declares.

A choice is a per-repository parameter that renders into the tool's owned
configuration as one top-level key. The policy exposes it with a default, and
a Manifest may set another value that the selected release accepts.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass

from gateseal.diagnostics import GatesealError
from gateseal.validation import fields, mapping, string, version, version_key

NAME = re.compile(r"[a-z][a-z0-9_]*")
"""A choice's name, as choices.<capability>.<name> names it."""

KEY = re.compile(r"[a-z][a-z0-9-]*")

EXTEND = "extend"
"""The key that would make a configuration load another one, which no policy
configuration and so no choice may set."""

VALUE = re.compile(r"[a-z0-9]+")
"""A value a choice accepts, which renders as a TOML string without escaping."""


@dataclass(frozen=True)
class Choice:
    """One choice: the key it renders into, and the values the tool accepts.

    since maps each accepted value, in definition order, to the oldest release
    of the tool that accepts it.
    """

    key: str
    since: Mapping[str, str]

    def parse(self, value: object, field: str) -> str:
        """Return value, or fail with invalid_choice unless it is an accepted one."""
        if not isinstance(value, str) or value not in self.since:
            raise GatesealError(
                "invalid_choice",
                f"{field} takes one of {', '.join(self.since)}, not {value!r}.",
            )
        return value

    def require(self, value: str, name: str, release: str, field: str) -> None:
        """Fail with invalid_choice unless release of the tool name accepts value."""
        if version_key(release) < version_key(self.since[value]):
            accepted = [
                item
                for item, since in self.since.items()
                if version_key(since) <= version_key(release)
            ]
            raise GatesealError(
                "invalid_choice",
                f"{field} is {value}, which {name} {release} does not accept. "
                + (
                    f"{name} {release} accepts {', '.join(accepted)}."
                    if accepted
                    else f"gateseal knows no value that {name} {release} accepts."
                ),
                next_step=f"Choose a value that {name} {release} accepts, or "
                f"select a newer {name}.",
            )


def parse_choices(value: object, field: str) -> dict[str, Choice]:
    """Validate a definition's choices table; no two choices share a key."""
    found: dict[str, Choice] = {}
    for name, entry in mapping(value, field).items():
        table = fields(entry, {"key", "since"}, f"{field}.{name}")
        since = {
            item: version(release, f"{field}.{name}.since.{item}")
            for item, release in mapping(
                table["since"], f"{field}.{name}.since"
            ).items()
        }
        key = string(table["key"], f"{field}.{name}.key")
        if (
            not NAME.fullmatch(name)
            or not KEY.fullmatch(key)
            or key == EXTEND
            or not since
            or not all(map(VALUE.fullmatch, since))
        ):
            raise GatesealError(
                "invalid_field",
                f"{field}.{name} needs a lowercase name and a key other than "
                f"{EXTEND}, and a since table that maps lowercase values to stable "
                "releases.",
            )
        found[name] = Choice(key, since)
    if len({choice.key for choice in found.values()}) != len(found):
        raise GatesealError(
            "invalid_field", f"{field} declares two choices with one key."
        )
    return found
