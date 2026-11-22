"""The npm-package kind: a tool from the npm registry, on the policy's Node.

Each hook is one command on the policy's Node, which node.Node.launch
renders, so two hooks with the same packages give the same bytes and share
one install. Each hook excludes every file that gateseal writes, so a
formatter never rewrites one.

Each version's Policy Lock record is its package and the tool's packages.
The Lock records each tool in components under its name, and its Node once
in native_artifacts.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import ClassVar, cast

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
    ToolDefinition,
    ToolUse,
    Upstream,
    changed_upstream,
    exclusion,
    local_repository,
    lock_failure,
)
from gateseal.tools.node import Node, Package, package_name, require_released
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
        """Require each version's record to be the one gateseal policy lock writes.

        A record whose package or packages differ from the definition's and
        policy.toml's fails with policy_lock_stale.
        """
        name, executable = definition.name, definition.executable
        if executable is None or node is None:
            raise GatesealError(
                "invalid_field", f"tools.{name} runs on Node with an executable."
            )
        packages, expected = self._packages(name, catalog), self.record(catalog)
        for version in catalog.requires:
            field = f"components.{name}.versions.{version}"
            recorded = fields(records[version], {"package", "packages"}, field)
            if recorded != expected:
                raise lock_failure(
                    "policy_lock_stale",
                    f"policy.lock records {json.dumps(recorded)} for {name} "
                    f"{version}, but its definition and policy.toml give "
                    f"{json.dumps(expected)}.",
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
            packages,
        )

    def lock(
        self,
        definition: ToolDefinition,
        catalog: Catalog,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Record the package and the packages, once the registry has each release.

        The package is the definition's fact: a recorded one that differs
        fails with upstream_changed. The packages are policy.toml's, so a
        recorded set that differs is replaced. The registry is asked only for
        a package version that the record lacks.
        """
        name = definition.name
        packages = (Package(self.package, version), *self._packages(name, catalog))
        record = self.record(catalog)
        known: Mapping[str, object] = {}
        if recorded is not None:
            found = nested(recorded, "packages")
            if nested(recorded, "package") != self.package or not isinstance(
                found, dict
            ):
                raise changed_upstream(
                    name, version, "its definition", recorded, record
                )
            known = cast(dict[str, object], found) | {self.package: version}
        require_released(packages, known, upstream, name, version)
        return record

    def record(self, catalog: Catalog) -> dict[str, object]:
        """Return a version's Policy Lock record: the package and the packages."""
        return {"package": self.package, "packages": dict(catalog.packages)}

    def _packages(self, name: str, catalog: Catalog) -> tuple[Package, ...]:
        """Return the tool's packages by name, which never hold its own package.

        Its own package twice would install two versions under one name.
        """
        if self.package in catalog.packages:
            raise GatesealError(
                "duplicate_identifier",
                f"policy.toml pins {self.package} in the packages of {name}, but "
                f"{self.package} is the tool's own package, which its pin selects.",
                next_step=f"Remove {self.package} from the packages of {name}.",
            )
        return tuple(Package(*item) for item in catalog.packages.items())


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
    packages: tuple[Package, ...]

    @property
    def release(self) -> Release:
        return self.versions[self.version]

    def select(self, version: str) -> NpmTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> NpmTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    def program(self, variables: Collection[str] = ()) -> tuple[str, ...]:
        """Return the command that installs the packages if needed and starts the tool.

        mise passes the caller's whole environment on, so variables adds nothing.
        """
        packages = (Package(self.source.package, self.version), *self.packages)
        return self.node.launch(packages, self.executable.name)

    def entry(self, exclude: str | None) -> dict[str, object]:
        """Return the local repository, whose hooks never get a generated file."""
        pattern = GENERATED if exclude is None else f"{GENERATED}|{exclude}"
        definition = self.definition
        return local_repository(
            self.program(), self.use.hooks, pattern, definition.stages, definition.scope
        )

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the components record. The Lock records the Node apart."""
        return (
            "components",
            self.definition.name,
            {
                "package": self.source.package,
                "version": self.version,
                "packages": {
                    package.name: package.version for package in self.packages
                },
                "hook_ids": [hook.id for hook in self.use.hooks],
            },
        )
