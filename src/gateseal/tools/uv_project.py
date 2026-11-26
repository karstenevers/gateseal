"""The uv-project kind: a PyPI package that runs in the repository's uv project.

A tool such as a type checker resolves imports from the packages that the
project installs, so it cannot run without them. Each hook of this kind is one
uv run command, which starts the tool in the project.

Guarantees this module owns, whatever a definition says:

- Exact pin: each hook names the package at its selected version with --with.
  uv installs that version in a layer over the tool's environment, where it
  wins over another version of the package that the project pins. gateseal
  policy lock checks that PyPI has each reviewed release. As for a hook
  repository's own dependencies, the install has no wheel hash. So the pin is
  a name and a version, and the project's uv settings choose the index.
- The project's package never runs in the tool's place: uv skips the layer
  when the environment already holds the package at that version, and checks
  no hash for it. So every command that loads the Lock, and the commit check
  with the staged uv.lock, refuses a uv.lock that installs the tool's package
  at its pin from a source other than a package index, such as a path.
- Its own exact environment: each tool runs in .gateseal/environments/<tool>,
  which uv creates at the project root and Git ignores. With --locked and
  --exact, uv installs what the repository's uv.lock pins, removes every other
  package, and stops the hook when uv.lock is missing or no longer matches
  pyproject.toml. So a commit sees the packages that a new clone, as the CI
  gate checks one, installs. In a uv workspace it installs every member's
  packages. uv installs the default dependency groups, and the extras and
  groups that the definition names. The hook sets UV_PROJECT_ENVIRONMENT
  over the caller's and never reads or changes the developer's .venv.
- A project: outside a project uv ignores --locked and still starts the tool,
  which would then check without the project's packages. So configure and
  every command that loads the Lock require pyproject.toml and uv.lock at the
  repository root, and refuse a pyproject.toml that sets tool.uv.managed to
  false or a uv.lock that is a link. The commit check requires only a staged
  uv.lock, as a regular TOML file, and does not read pyproject.toml.

The tool runs on the project's Python, as uv selects it. Its hooks get the
caller's environment, as a hook repository's do, so uv reads the caller's UV_
settings and the program reads pre-commit's variables, such as
PRE_COMMIT_FROM_REF. The first use installs the project's packages and the
tool, which needs the network.

The layer's packages come first on that Python's import path, the tool's own
dependencies among them. So a definition makes an analyzer read the tool's
environment by its path, as the bundled Pyright rules do with venvPath and
venv. An analyzer that asks the Python that starts it would read the layer's
version of a package that the tool and the project both install.

One limit is uv's, for a tool that imports the project's packages itself:
when the path of its cache holds a space, uv 0.13.6 and 0.13.9 start the
program outside the layer, without the project's packages.

Each version's Policy Lock record is its package. The Lock records each tool
in components under its name.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from gateseal.diagnostics import GatesealError
from gateseal.managed_files import MANIFEST
from gateseal.tools.model import (
    OWNED,
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
from gateseal.validation import (
    fields,
    mapping,
    nested,
    parse_toml,
    sequence,
    string,
)
from gateseal.validation import version as stable_version

if TYPE_CHECKING:
    from gateseal.tools.node import Node

ENVIRONMENTS = f"{OWNED}/environments"
"""The folder of each tool's environment, relative to the project root."""

UV_RUN = ("uv", "run", "--locked", "--exact", "--all-packages")
"""Start a program in the project, with exactly what uv.lock pins.

--exact removes every package that uv.lock does not pin from the environment.
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
NAME = re.compile(_NAME)
"""The name of a project's extra or dependency group."""
OPTIONAL = {"extras", "groups", "default_groups"}
"""The keys of [source] that a definition may leave out."""
LOCAL_SOURCES = frozenset({"path", "directory", "editable"})
"""The uv.lock sources whose location a message shows. A URL can hold a token."""
INDEX_OR_VIRTUAL = frozenset({"registry", "virtual"})
"""The uv.lock sources that cannot shadow a tool.

A package index serves reviewed releases. uv never installs a virtual source,
the project when it is not a package.
"""

LOCKED_VERSION = re.compile(r"(?:0!)?(?P<release>[0-9]+(?:\.[0-9]+)*)(?:\+[a-z0-9.]+)?")
"""A version in uv.lock without a pre, post or dev segment."""


@dataclass(frozen=True)
class UvProject:
    """The [source] of a tool that runs in the uv project: its PyPI package.

    extras and groups name the project's extras and dependency groups that
    the tool's environment installs; the tool's own extras are in package's
    brackets. default_groups false leaves out the project's default groups.
    """

    kind: ClassVar[str] = "uv-project"
    starts: ClassVar[Starts] = Starts.EXECUTABLE
    remark: ClassVar[str] = "; the repository's uv.lock pins what it imports"
    on_node: ClassVar[bool] = False
    scans_history: ClassVar[bool] = True
    package: str
    extras: tuple[str, ...]
    groups: tuple[str, ...]
    default_groups: bool

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
        use: ToolUse,
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
    table = mapping(value, field)
    table = fields(table, {"package"} | (OPTIONAL & set(table)), field)
    package = string(table["package"], f"{field}.package")
    if not PACKAGE.fullmatch(package):
        raise GatesealError(
            "invalid_field",
            f"{field}.package must be a package's name on PyPI, with any extras in "
            f"brackets, such as pyright[nodejs], not {package!r}.",
        )
    default_groups = table.get("default_groups", True)
    if not isinstance(default_groups, bool):
        raise GatesealError(
            "invalid_field", f"{field}.default_groups must be true or false."
        )
    return UvProject(
        package,
        _names(table.get("extras", []), f"{field}.extras"),
        _names(table.get("groups", []), f"{field}.groups"),
        default_groups,
    )


def _names(value: object, field: str) -> tuple[str, ...]:
    """Return the project's extras or groups that value lists, each once."""
    names = tuple(string(item, field) for item in sequence(value, field))
    for name in names:
        if not NAME.fullmatch(name):
            raise GatesealError(
                "invalid_field",
                f"{field} must list names of the project, such as dev, not {name!r}.",
            )
    if len(set(names)) < len(names):
        raise GatesealError("invalid_field", f"{field} lists a name more than once.")
    return names


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
        """Return the command that starts the tool in its environment.

        uv passes the caller's whole environment on, so variables adds nothing.
        """
        source = self.source
        return (
            "env",
            f"UV_PROJECT_ENVIRONMENT={ENVIRONMENTS}/{self.definition.name}",
            *UV_RUN,
            *(option for extra in source.extras for option in ("--extra", extra)),
            *(option for group in source.groups for option in ("--group", group)),
            *(() if source.default_groups else ("--no-default-groups",)),
            "--with",
            f"{source.package}=={self.version}",
            "--",
            self.executable.name,
        )

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
    """Require the repository at root to be a locked uv project for such a tool.

    Its uv.lock must be a regular file, as the commit check reads it, and must
    not install a tool's package from the project's source.
    """
    packages: dict[str, tuple[str, str]] = {}
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
        if (root / PROJECT_FILES[1]).is_symlink():
            raise GatesealError(
                "missing_project",
                f"{runs} {PROJECT_FILES[1]} is a link, and the commit check reads "
                "only a regular file.",
                next_step=f"Replace {PROJECT_FILES[1]} with the file that it links "
                f"to, or {stop} gateseal configure.",
            )
        if _unmanaged(root):
            raise GatesealError(
                "missing_project",
                f"{runs} {PROJECT_FILES[0]} sets tool.uv.managed to false, so uv "
                "runs no project.",
                next_step=f"Remove managed = false from [tool.uv], or {stop} "
                "gateseal configure.",
            )
        packages[tool.definition.name] = (tool.source.package, tool.version)
    if packages:
        refuse_shadowing(packages, (root / PROJECT_FILES[1]).read_bytes())


def locked_packages(components: Mapping[str, object]) -> dict[str, tuple[str, str]]:
    """Return each Project Tool's package and version that the Lock records.

    A record with a package is a Project Tool's, unless it lists packages too,
    as the record of a tool from npm does.
    """
    packages: dict[str, tuple[str, str]] = {}
    for name, value in components.items():
        field = f"components.{name}"
        record = mapping(value, field)
        if "package" not in record or "packages" in record:
            continue
        record = fields(record, {"package", "version", "hook_ids"}, field)
        packages[name] = (
            string(record["package"], f"{field}.package"),
            stable_version(record["version"], f"{field}.version"),
        )
    return packages


def refuse_shadowing(packages: Mapping[str, tuple[str, str]], uv_lock: bytes) -> None:
    """Fail when uv.lock installs a tool's package at its pin from another source.

    uv then skips the tool's --with layer and runs the project's package. A
    source other than a package index has no reviewed release.
    """
    sourced = list(_sourced_packages(uv_lock))
    for tool, (package, pin) in packages.items():
        distribution = package.partition("[")[0]
        for name, locked_version, kind, location in sourced:
            if _normalized(name) != _normalized(distribution) or not _same_release(
                locked_version, pin
            ):
                continue
            shown = f" {location!r}" if kind in LOCAL_SOURCES else ""
            raise GatesealError(
                "shadowed_tool",
                f"uv.lock installs {distribution} {locked_version} from its {kind} "
                f"source{shown}, so the project's own package would run in place of "
                f"the reviewed {tool}.",
                next_step=f"Install {distribution} from a package index, or give the "
                f"project's own {distribution} another version. Then run uv lock.",
            )


def _sourced_packages(uv_lock: bytes) -> Iterator[tuple[str, str, str, object]]:
    """Yield each package of uv.lock that could shadow a tool.

    Each is its name, its version, its source's kind and the source's location.
    A uv.lock that is not TOML, or whose packages it cannot read, is uv's to
    report.
    """
    try:
        locked = parse_toml(uv_lock).get("package")
    except GatesealError:
        return
    for entry in cast(list[object], locked) if isinstance(locked, list) else []:
        name, version, source = (
            nested(entry, key) for key in ("name", "version", "source")
        )
        if (
            isinstance(name, str)
            and isinstance(version, str)
            and isinstance(source, dict)
            and source
            and source.keys().isdisjoint(INDEX_OR_VIRTUAL)
        ):
            kind, location = next(iter(cast(dict[str, object], source).items()))
            yield name, version, kind, location


def _normalized(name: str) -> str:
    """Return a package's name as PEP 503 compares it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _same_release(locked: str, pin: str) -> bool:
    """Say whether a uv.lock version satisfies ==pin under PEP 440.

    The release segments compare without trailing zeros, the epoch must be 0,
    and a local label matches; a pre, post or dev release does not.
    """
    found = LOCKED_VERSION.fullmatch(locked)
    return found is not None and _release(found["release"]) == _release(pin)


def _release(text: str) -> tuple[int, ...]:
    parts = [int(part) for part in text.split(".")]
    while parts[-1] == 0 and len(parts) > 1:
        parts.pop()
    return tuple(parts)
