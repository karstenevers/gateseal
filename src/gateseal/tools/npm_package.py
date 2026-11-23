"""The npm-package kind: a tool from the npm registry, on the policy's Node.

Each version's Policy Lock record is npm's lockfile of the tool's package and
its packages, which configure writes as an owned output. Each hook is one
command that node.Node.launch renders, and excludes every file that gateseal
writes, so a formatter never rewrites one. The Lock records each tool in
components under its name, and its Node once in native_artifacts.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import ClassVar

from gateseal.ci_providers import PROVIDERS
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import CONFIG, LOCK, MANIFEST
from gateseal.tools.model import (
    OWNED,
    Catalog,
    Executable,
    Hook,
    LockSection,
    Release,
    Starts,
    Tool,
    ToolDefinition,
    ToolUse,
    Upstream,
    exclusion,
    local_repository,
)
from gateseal.tools.node import Node, Tree, package_name, read_tree
from gateseal.validation import fields, nested

GENERATED = exclusion(
    tuple(
        sorted(
            {OWNED, CONFIG, LOCK, MANIFEST}
            | {provider.path for provider in PROVIDERS.values()}
        )
    )
)
"""pre-commit's exclude pattern of every file that gateseal writes.

They are the owned outputs, the native configuration, every CI provider's
file, the Lock and the Manifest, whose bytes the Lock pins or holds.
"""


@dataclass(frozen=True)
class NpmPackage:
    """The [source] of a tool from npm: its package's name on the registry."""

    kind: ClassVar[str] = "npm-package"
    starts: ClassVar[Starts] = Starts.EXECUTABLE
    remark: ClassVar[str] = ""
    on_node: ClassVar[bool] = True
    scans_history: ClassVar[bool] = True
    package: str

    def offer(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        records: Mapping[str, object],
        node: Node | None,
    ) -> NpmTool:
        """Require each version's record to lock the tool's package and packages.

        A record that locks others fails with policy_lock_stale.
        """
        name, executable = definition.name, definition.executable
        if executable is None or node is None:
            raise GatesealError(
                "invalid_field", f"tools.{name} runs on Node with an executable."
            )
        trees: dict[str, Tree] = {}
        for version in catalog.requires:
            field = f"components.{name}.versions.{version}"
            trees[version] = read_tree(
                fields(records[version], {"lockfile"}, field)["lockfile"],
                self.dependencies(name, catalog, version),
                self.package,
                executable.name,
                f"{name} {version}",
            )
        versions = {
            version: Release(version, requires)
            for version, requires in catalog.requires.items()
        }
        return NpmTool(
            definition,
            self,
            executable,
            use,
            catalog.pin,
            versions,
            catalog.pin,
            node,
            trees,
        )

    def lock(
        self,
        definition: ToolDefinition,
        catalog: Catalog,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Record npm's lockfile of the package and the packages.

        A recorded lockfile of the same packages stays, so npm resolves only
        a version or a set of packages that the record lacks.
        """
        dependencies = self.dependencies(definition.name, catalog, version)
        lockfile = nested(recorded, "lockfile")
        if nested(lockfile, "packages", "", "dependencies") != dependencies:
            lockfile = upstream.lockfile(dependencies, definition.name, version)
        return {"lockfile": lockfile}

    def dependencies(self, name: str, catalog: Catalog, version: str) -> dict[str, str]:
        """Return what the tool installs: its package at version and its packages.

        Its own package among its packages would install two versions under one name.
        """
        if self.package in catalog.packages:
            raise GatesealError(
                "duplicate_identifier",
                f"policy.toml pins {self.package} in the packages of {name}, but "
                f"{self.package} is the tool's own package, which its pin selects.",
                next_step=f"Remove {self.package} from the packages of {name}.",
            )
        return {self.package: version, **catalog.packages}


def parse_source(value: object, field: str) -> NpmPackage:
    table = fields(value, {"package"}, field)
    return NpmPackage(package_name(table["package"], f"{field}.package"))


@dataclass(frozen=True)
class NpmTool:
    """A tool from npm as a policy offers it, at its selected version."""

    definition: ToolDefinition
    source: NpmPackage
    executable: Executable
    use: ToolUse
    pin: str
    versions: Mapping[str, Release]
    version: str
    node: Node
    trees: Mapping[str, Tree]

    @property
    def release(self) -> Release:
        return self.versions[self.version]

    @property
    def tree(self) -> Tree:
        return self.trees[self.version]

    @property
    def lockfile(self) -> str:
        return f"{OWNED}/{self.definition.name}.package-lock.json"

    def select(self, version: str) -> NpmTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> NpmTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    def program(self, variables: Collection[str] = ()) -> tuple[str, ...]:
        """Return the command that installs the tree if needed and starts the tool.

        mise passes the caller's whole environment on, so variables adds nothing.
        """
        return self.node.launch(self.tree, self.lockfile, self.executable.name)

    def entry(self, exclude: str | None) -> dict[str, object]:
        """Return the local repository, whose hooks never get a generated file."""
        pattern = GENERATED if exclude is None else f"{GENERATED}|{exclude}"
        definition = self.definition
        return local_repository(
            self.program(), self.use.hooks, pattern, definition.stages, definition.scope
        )

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the components record. The Lock records the Node apart."""
        packages = dict(self.tree.dependencies)
        del packages[self.source.package]
        return (
            "components",
            self.definition.name,
            {
                "package": self.source.package,
                "version": self.version,
                "packages": packages,
                "hook_ids": [hook.id for hook in self.use.hooks],
            },
        )


def lockfiles(tools: Iterable[Tool]) -> dict[str, bytes]:
    """Return the lockfile of each tool from npm at its selected version, by path."""
    return {
        tool.lockfile: tool.tree.lockfile for tool in tools if isinstance(tool, NpmTool)
    }
