"""The npm-package kind: a tool from the npm registry, on the policy's Node.

Each version's Policy Lock record is npm's lockfile of the tool's package and
its packages, which configure writes as an owned output, and the rules of the
presets that an ESLint rule file names, which eslint_presets reads. Each hook is
gateseal's own hook, npm_tool, with the tool's name and its executable's, and
excludes every file that gateseal writes, so a formatter never rewrites one.
The Lock records each tool in components under its name, and its Node once in
native_artifacts.
"""

from __future__ import annotations

import sys
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from typing import ClassVar

from gateseal import npm_tool
from gateseal.ci_providers import PROVIDERS
from gateseal.compiler_release import NPM_TOOL_HOOK, GatesealPin
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import CONFIG, LOCK, MANIFEST
from gateseal.tools import eslint_presets
from gateseal.tools.configurations import Presets
from gateseal.tools.eslint_presets import PRESETS
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
)
from gateseal.tools.node import Node, Tree, package_name, read_tree
from gateseal.validation import fields, mapping, nested

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
        """Require each version's record to lock the tool's package and packages,
        and to hold the presets that its rule file names.

        A record that locks others, or holds other presets, fails with
        policy_lock_stale.
        """
        name, executable = definition.name, definition.executable
        if executable is None or node is None:
            raise GatesealError(
                "invalid_field", f"tools.{name} runs on Node with an executable."
            )
        trees: dict[str, Tree] = {}
        presets: dict[str, Presets] = {}
        for version in catalog.requires:
            field = f"components.{name}.versions.{version}"
            record = mapping(records[version], field)
            record = fields(record, {"lockfile"} | ({PRESETS} & record.keys()), field)
            trees[version] = read_tree(
                record["lockfile"],
                self.dependencies(name, catalog, version),
                self.package,
                executable.name,
                f"{name} {version}",
            )
            presets[version] = eslint_presets.read(
                definition, use, version, record.get(PRESETS)
            )
        versions = {
            version: Release(version, requires, presets=presets[version])
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
        use: ToolUse,
        catalog: Catalog,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Record npm's lockfile of the package and the packages, and the presets.

        A recorded lockfile of the same packages stays, and so do its presets,
        so npm resolves only a version or a set of packages that the record
        lacks. A new lockfile drops the recorded presets: each is read again.
        """
        dependencies = self.dependencies(definition.name, catalog, version)
        lockfile = nested(recorded, "lockfile")
        if nested(lockfile, "packages", "", "dependencies") != dependencies:
            lockfile = upstream.lockfile(dependencies, definition.name, version)
            recorded = None
        presets = eslint_presets.lock(
            definition, use, version, lockfile, nested(recorded, PRESETS), upstream
        )
        return {"lockfile": lockfile} | ({PRESETS: presets} if presets else {})

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
        return npm_tool.LOCKFILE.format(tool=self.definition.name)

    def select(self, version: str) -> NpmTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> NpmTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    @property
    def hook_args(self) -> tuple[str, str]:
        """Return what gateseal's hook needs to start the tool: its name and
        its executable's. The hook reads the rest from the Lock."""
        return self.definition.name, self.executable.name

    def program(self, variables: Collection[str] = ()) -> tuple[str, ...]:
        """Return the command that starts the tool as its hooks do, in the
        gateseal that runs.

        The command runs in the consumer's repository, so Python starts the
        hook's file in isolated mode: no module of the working folder or of
        PYTHONPATH loads in place of gateseal's. The hook needs only the
        standard library. The caller's whole environment passes on, so
        variables adds nothing.
        """
        return (sys.executable, "-I", npm_tool.__file__, *self.hook_args)

    def entry(self, exclude: str | None, pin: GatesealPin) -> dict[str, object]:
        """Return gateseal's repository with its hook once for each hook.

        Each one keeps its ID as the alias, and never gets a generated file.
        pre-commit starts one process of a hook at a time, because two first
        starts of one program at once would both install it.
        """
        pattern = GENERATED if exclude is None else f"{GENERATED}|{exclude}"
        definition = self.definition
        return pin.repository_with(
            [
                hook.base_entry(pattern, definition.stages, definition.scope)
                | {
                    "id": NPM_TOOL_HOOK,
                    "alias": hook.id,
                    "args": [*self.hook_args, *hook.args],
                }
                for hook in self.use.hooks
            ]
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
