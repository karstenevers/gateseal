"""The uv-project kind: a PyPI package that runs in the repository's uv project.

A tool such as a type checker resolves imports from the packages that the
project installs, so it cannot run in an environment of its own. Each hook of
this kind is one uv run command, which starts the tool in the project.

Guarantees this module owns, whatever a definition says:

- Exact pin: each hook names the package at its selected version with --with.
  uv installs that version in a layer over the project's environment, where
  it wins over another version of the package that the project pins. gateseal
  policy lock checks that PyPI has each reviewed release. As for a hook
  repository's own dependencies, the install has no wheel hash. So the pin is
  a name and a version, and the project's uv settings choose the index. A
  package of that name and version that the project installs from another
  source, such as a path, runs in the tool's place.
- The project's packages: with --locked, uv installs what the repository's
  uv.lock pins into the project's environment, .venv, and stops the hook when
  uv.lock is missing or no longer matches pyproject.toml. In a uv workspace
  it installs every member's packages. uv run removes nothing from .venv, so
  a package that uv.lock no longer holds stays there until uv sync. A new
  clone, as the CI gate checks one, has no such package.
- A project: outside a project uv ignores --locked and still starts the tool,
  which would then check without the project's packages. So configure and
  every command that loads the Lock require pyproject.toml and uv.lock at the
  repository root, and refuse a pyproject.toml that sets tool.uv.managed to
  false. A commit through pre-commit alone has no such check.

The tool runs on the project's Python, as uv selects it. Its hooks get the
caller's environment, as a hook repository's do, so uv reads the caller's UV_
settings and the program reads pre-commit's variables, such as
PRE_COMMIT_FROM_REF. The first use installs the project's packages and the
tool, which needs the network.

The layer's packages come first on that Python's import path, the tool's own
dependencies among them. So a definition makes an analyzer read the project's
environment by name, as the bundled Pyright rules do with venvPath and venv.
An analyzer that asks the Python that starts it would read the layer's
version of a package that the tool and the project both install.

One limit is uv's, for a tool that imports the project's packages itself:
when the path of its cache holds a space, uv 0.13.6 and 0.13.9 start the
program outside the layer, without the project's packages.

Each version's Policy Lock record is its package. The Lock records each tool
in components under its name.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from gateseal.diagnostics import GatesealError
from gateseal.managed_files import MANIFEST
from gateseal.tools.model import (
    Catalog,
    Executable,
    Hook,
    LockSection,
    MissingUpstream,
    Release,
    Starts,
    Tool,
    ToolDefinition,
    ToolUse,
    Upstream,
    changed_upstream,
    local_repository,
    lock_failure,
)
from gateseal.validation import fields, nested, parse_toml, string

if TYPE_CHECKING:
    from gateseal.tools.node import Node

UV_RUN = ("uv", "run", "--locked", "--all-packages", "--with")
"""Start a program in the project, with what uv.lock pins and one more package.

--all-packages installs every member of a uv workspace with its dependencies.
Without it, uv installs only the root project's, and a tool that reads the
whole repository misses what a member alone depends on.
"""

INDEX = "https://pypi.org/pypi"
"""Where gateseal policy lock asks whether a release exists."""

PROJECT_FILES = ("pyproject.toml", "uv.lock")
"""The files at the repository root that make it a locked uv project."""

_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
PACKAGE = re.compile(rf"(?P<name>{_NAME})(?:\[{_NAME}(?:,{_NAME})*\])?")
"""A package's name on PyPI, with the extras to install in brackets."""


@dataclass(frozen=True)
class UvProject:
    """The [source] of a tool that runs in the uv project: its PyPI package."""

    kind: ClassVar[str] = "uv-project"
    starts: ClassVar[Starts] = Starts.EXECUTABLE
    remark: ClassVar[str] = "; the repository's uv.lock pins what it imports"
    on_node: ClassVar[bool] = False
    package: str

    def offer(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        records: Mapping[str, object],
        node: Node | None,
    ) -> ProjectTool:
        """Require each version's record to name the definition's package."""
        name, executable = definition.name, definition.executable
        if executable is None:
            raise GatesealError("invalid_field", f"tools.{name} names no executable.")
        for version in catalog.requires:
            field = f"components.{name}.versions.{version}"
            recorded = fields(records[version], {"package"}, field)["package"]
            if recorded != self.package:
                raise lock_failure(
                    "policy_lock_stale",
                    f"policy.lock records {name} {version} for the package "
                    f"{recorded!r}, but its definition names {self.package}.",
                )
        versions = {
            version: Release(version, requires)
            for version, requires in catalog.requires.items()
        }
        return ProjectTool(
            definition, self, executable, use, catalog.pin, versions, catalog.pin
        )

    def lock(
        self,
        definition: ToolDefinition,
        catalog: Catalog,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Record the package, once PyPI has its release version.

        The package is the definition's fact. A recorded one that differs
        fails like a changed upstream fact, so no lock replaces it unseen.
        """
        record: dict[str, object] = {"package": self.package}
        if recorded is not None and recorded != record:
            raise changed_upstream(
                definition.name, version, "its definition", recorded, record
            )
        distribution = self.package.partition("[")[0]
        if upstream.download(f"{INDEX}/{distribution}/{version}/json") is None:
            raise MissingUpstream(
                "upstream_release_missing",
                f"PyPI has no release {version} of {distribution}, the package of "
                f"{definition.name}.",
                definition.name,
                version,
            )
        return record


def parse_source(value: object, field: str) -> UvProject:
    table = fields(value, {"package"}, field)
    package = string(table["package"], f"{field}.package")
    if not PACKAGE.fullmatch(package):
        raise GatesealError(
            "invalid_field",
            f"{field}.package must be a package's name on PyPI, with any extras in "
            f"brackets, such as pyright[nodejs], not {package!r}.",
        )
    return UvProject(package)


@dataclass(frozen=True)
class ProjectTool:
    """A tool of the uv project as a policy offers it, at its selected version."""

    definition: ToolDefinition
    source: UvProject
    executable: Executable
    use: ToolUse
    pin: str
    versions: Mapping[str, Release]
    version: str

    @property
    def release(self) -> Release:
        return self.versions[self.version]

    @property
    def node(self) -> None:
        return None

    def select(self, version: str) -> ProjectTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> ProjectTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    def program(self, variables: Collection[str] = ()) -> tuple[str, ...]:
        """Return the command that starts the tool in the project.

        uv passes the caller's whole environment on, so variables adds nothing.
        """
        requirement = f"{self.source.package}=={self.version}"
        return (*UV_RUN, requirement, "--", self.executable.name)

    def entry(self, exclude: str | None) -> dict[str, object]:
        definition = self.definition
        return local_repository(
            self.program(), self.use.hooks, exclude, definition.stages, definition.scope
        )

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the components record."""
        return (
            "components",
            self.definition.name,
            {
                "package": self.source.package,
                "version": self.version,
                "hook_ids": [hook.id for hook in self.use.hooks],
            },
        )


def _unmanaged(root: Path) -> bool:
    """Say whether pyproject.toml turns uv's project off with managed = false.

    A file that is not TOML is uv's to report, when the hook runs.
    """
    try:
        document = parse_toml((root / PROJECT_FILES[0]).read_bytes())
    except GatesealError:
        document = {}
    return nested(document, "tool", "uv", "managed") is False


def require_project(root: Path, tools: Iterable[Tool]) -> None:
    """Require the repository at root to be a locked uv project for such a tool."""
    for tool in tools:
        if not isinstance(tool, ProjectTool):
            continue
        runs = f"{tool.definition.name} runs in the repository's uv project, but"
        stop = f"stop selecting {tool.use.capability} in {MANIFEST}. Then run"
        for file in PROJECT_FILES:
            if (root / file).is_file():
                continue
            raise GatesealError(
                "missing_project",
                f"{runs} the repository has no {file}. Only uv projects are supported.",
                next_step=f"Create the project's {file} with uv and commit it, or "
                f"{stop} gateseal configure.",
            )
        if _unmanaged(root):
            raise GatesealError(
                "missing_project",
                f"{runs} {PROJECT_FILES[0]} sets tool.uv.managed to false, so uv "
                "runs no project.",
                next_step=f"Remove managed = false from [tool.uv], or {stop} "
                "gateseal configure.",
            )
