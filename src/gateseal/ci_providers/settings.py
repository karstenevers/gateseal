"""The Manifest's [ci] table: which provider, when its pipeline runs, and where.

A pipeline of its own has Triggers. With standalone = false the team's own
pipeline includes the job and owns its triggers, so the table names none.
Each provider owns the key that names a machine of the team's own.
"""

import re
from dataclasses import dataclass

from gateseal import __version__
from gateseal.ci_providers import PROVIDERS
from gateseal.ci_providers.model import Triggers
from gateseal.diagnostics import GatesealError
from gateseal.validation import mapping, sequence, string

BRANCH = re.compile(r"[A-Za-z0-9._/-]+")
TIMEOUT_MINUTES = 30
LONGEST_MINUTES = 360
"""The longest job on a GitHub-hosted runner."""
TRIGGER_KEYS = ("default_branch", "branches", "schedule")
KEYS = {"provider", "standalone", "timeout_minutes", *TRIGGER_KEYS}


@dataclass(frozen=True)
class CI:
    """The CI provider whose file configure writes, and how its job runs.

    triggers is None when the team's own pipeline includes the job. runner is
    empty for the hosted image; otherwise it holds the provider's runner key's
    value: the pool on Azure Pipelines, the labels on GitHub.
    """

    provider: str
    triggers: Triggers | None
    timeout_minutes: int
    runner: tuple[str, ...]


def parse_ci(value: object) -> CI:
    """Validate the [ci] table. Every invalid value names its key."""
    table = mapping(value, "manifest.ci")
    if "provider" not in table:
        raise GatesealError("invalid_field", "manifest.ci requires provider.")
    provider = string(table["provider"], "ci.provider")
    if provider not in PROVIDERS:
        raise GatesealError(
            "unsupported_ci_provider",
            f"ci.provider is {provider!r}, which gateseal {__version__} does not "
            "implement.",
            next_step="Use a gateseal compiler that implements this CI provider.",
        )
    own = PROVIDERS[provider].runner_key
    unknown = sorted(set(table) - KEYS - {own})
    if unknown:
        raise GatesealError(
            "invalid_field",
            f"ci.{unknown[0]} is not a key of manifest.ci for provider {provider}; "
            f"it takes {', '.join(sorted(KEYS | {own}))}.",
        )
    standalone = _boolean(table.get("standalone", True), "standalone")
    return CI(
        provider,
        _triggers(table) if standalone else _without_triggers(table),
        _timeout(table.get("timeout_minutes", TIMEOUT_MINUTES)),
        PROVIDERS[provider].runner(table[own]) if own in table else (),
    )


def _without_triggers(table: dict[str, object]) -> None:
    for name in TRIGGER_KEYS:
        if name in table:
            raise GatesealError(
                "invalid_field",
                f"ci.{name} sets when a pipeline of its own runs, but with "
                "standalone = false the team's own pipeline decides that.",
                next_step=f"Remove ci.{name}, or remove standalone = false.",
            )


def _triggers(table: dict[str, object]) -> Triggers:
    if "default_branch" not in table:
        raise GatesealError(
            "invalid_field",
            "manifest.ci requires default_branch, unless standalone = false.",
        )
    default = _branch_name(table["default_branch"], "ci.default_branch")
    seen = [default]
    for value in sequence(table.get("branches", []), "ci.branches"):
        entry = string(value, "ci.branches")
        _branch_name(entry.removesuffix("/*"), "ci.branches entry")
        if entry in seen:
            raise GatesealError(
                "invalid_field",
                f"ci.branches names {entry!r} more than once, counting "
                "ci.default_branch.",
            )
        seen.append(entry)
    return Triggers(
        default, tuple(seen[1:]), _boolean(table.get("schedule", False), "schedule")
    )


def _branch_name(value: object, field: str) -> str:
    """Return a branch name of letters, digits, ., _, - and /.

    Git refuses a ref that ends with a dot, has an empty part, or has a part
    that starts with a dot or ends with .lock, so no branch could match one.
    In ci.branches the name may be followed by /* for every branch below it.
    """
    branch = string(value, field)
    if (
        not BRANCH.fullmatch(branch)
        or ".." in branch
        or branch.endswith(".")
        or any(
            not part or part.startswith(".") or part.endswith(".lock")
            for part in branch.split("/")
        )
    ):
        raise GatesealError(
            "invalid_field",
            f"{field} {branch!r} is not a branch name of letters, digits, "
            "., _, - and /.",
        )
    return branch


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise GatesealError("invalid_field", f"ci.{name} must be true or false.")
    return value


def _timeout(value: object) -> int:
    if type(value) is not int or not 1 <= value <= LONGEST_MINUTES:
        raise GatesealError(
            "invalid_field",
            f"ci.timeout_minutes must be a whole number from 1 to {LONGEST_MINUTES}.",
        )
    return value
