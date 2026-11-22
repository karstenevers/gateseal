"""The pre-commit-repository kind: a Python-language pre-commit hook repository.

Guarantees this module owns, whatever a definition says:

- Exact pin: the Policy Lock records each reviewed version's full commit,
  which gateseal policy lock read from its release tag. The Lock records the
  selected one, and pre-commit clones exactly it.

pre-commit builds the repository's hook environment at its first use, on the
Python that pre-commit runs on, and installs what the repository's commit
names. A dependency that the commit names without a hash, as ruff-pre-commit
names Ruff by version, is not pinned further.

Each version's Policy Lock record is its revision. The Lock records each
tool in components under its name.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar

from gateseal.diagnostics import GatesealError
from gateseal.tools.model import (
    Catalog,
    Hook,
    LockSection,
    MissingUpstream,
    Release,
    Starts,
    ToolDefinition,
    ToolUse,
    Upstream,
    changed_upstream,
    https_url,
    pattern,
)
from gateseal.validation import fields, string

if TYPE_CHECKING:
    from gateseal.tools.node import Node

COMMIT = re.compile("[0-9a-f]{40}")


@dataclass(frozen=True)
class PreCommitRepository:
    """The [source] of a hook repository: its URL and its release tag pattern."""

    kind: ClassVar[str] = "pre-commit-repository"
    starts: ClassVar[Starts] = Starts.UPSTREAM_HOOKS
    remark: ClassVar[str] = ""
    on_node: ClassVar[bool] = False
    scans_history: ClassVar[bool] = False
    repository: str
    tag: str

    def offer(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        records: Mapping[str, object],
        node: Node | None,
    ) -> RepositoryTool:
        """Read each version's revision, a full lowercase commit, from its record."""
        versions: dict[str, RevisionRelease] = {}
        for version, requires in catalog.requires.items():
            field = f"components.{definition.name}.versions.{version}"
            record = fields(records[version], {"revision"}, field)
            revision = string(record["revision"], f"{field}.revision")
            if not COMMIT.fullmatch(revision):
                raise GatesealError(
                    "invalid_field", f"{field}.revision must be a full commit ID."
                )
            versions[version] = RevisionRelease(version, requires, revision)
        return RepositoryTool(definition, self, use, catalog.pin, versions, catalog.pin)

    def lock(
        self,
        definition: ToolDefinition,
        catalog: Catalog,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Read the commit that the version's release tag names."""
        name, tag = definition.name, self.tag.replace("{version}", version)
        commit = upstream.tagged(self.repository, tag)
        if commit is None:
            raise MissingUpstream(
                "upstream_tag_missing",
                f"{self.repository} has no tag {tag} for {name} {version}.",
                name,
                version,
            )
        record: dict[str, object] = {"revision": commit}
        if recorded is not None and recorded != record:
            raise changed_upstream(
                name, version, f"tag {tag} of {self.repository}", recorded, record
            )
        return record


def parse_source(value: object, field: str) -> PreCommitRepository:
    table = fields(value, {"repository", "tag"}, field)
    return PreCommitRepository(
        https_url(table["repository"], f"{field}.repository"),
        pattern(table["tag"], f"{field}.tag", ("version",)),
    )


@dataclass(frozen=True)
class RevisionRelease(Release):
    revision: str


@dataclass(frozen=True)
class RepositoryTool:
    """A hook repository as a policy offers it, at its selected version."""

    definition: ToolDefinition
    source: PreCommitRepository
    use: ToolUse
    pin: str
    versions: Mapping[str, RevisionRelease]
    version: str

    @property
    def release(self) -> RevisionRelease:
        return self.versions[self.version]

    @property
    def node(self) -> None:
        return None

    def select(self, version: str) -> RepositoryTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> RepositoryTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    def program(self, variables: Collection[str] = ()) -> None:
        return None

    def entry(self, exclude: str | None) -> dict[str, object]:
        hooks: list[object] = []
        for hook in self.use.hooks:
            entry = hook.base_entry(
                exclude, self.definition.stages, self.definition.scope
            )
            if hook.args:
                entry["args"] = list(hook.args)
            hooks.append(entry)
        return {
            "repo": self.source.repository,
            "rev": self.release.revision,
            "hooks": hooks,
        }

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the components record."""
        return (
            "components",
            self.definition.name,
            {
                "repository": self.source.repository,
                "version": self.version,
                "revision": self.release.revision,
                "hook_ids": [hook.id for hook in self.use.hooks],
            },
        )
