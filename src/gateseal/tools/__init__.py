"""The Tool Kinds this compiler implements, and its Built-in Tools.

Each kind is a module in this package, and KINDS maps its name to the parser of
its [source] table. A Built-in Tool is a Tool Definition in the package's
data/tools, <name>.tool.toml, so the gateseal commit that the Lock pins
covers it. The suffix keeps every tool from reading a definition as its own
configuration, as Ruff reads any ruff.toml it walks past. Kind modules import
gateseal.tools.model and the shared modules gateseal.tools.mise,
gateseal.tools.node and gateseal.tools.package_managers, never this package.
Only node_project builds on another kind, npm_package, which imports no kind,
so loading it cannot cycle.
"""

import functools
from collections.abc import Callable, Collection, Iterable, Mapping
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

from gateseal.compiler_release import OWN_HOOKS
from gateseal.diagnostics import GatesealError
from gateseal.tools import (
    node_project,
    npm_package,
    pre_commit_repository,
    release_binary,
    uv_project,
)
from gateseal.tools.model import (
    NAME,
    Origin,
    Source,
    Tool,
    ToolDefinition,
    parse_definition,
    unsupported,
)
from gateseal.validation import mapping, parse_toml, string

SUFFIX = ".tool.toml"

KINDS: Mapping[str, Callable[[object, str], Source]] = {
    node_project.NodeProject.kind: node_project.parse_source,
    npm_package.NpmPackage.kind: npm_package.parse_source,
    pre_commit_repository.PreCommitRepository.kind: pre_commit_repository.parse_source,
    release_binary.ReleaseBinary.kind: release_binary.parse_source,
    uv_project.UvProject.kind: uv_project.parse_source,
}
"""Each kind's parser of a definition's [source], by its value of kind."""

PROJECT_CHECKS: tuple[Callable[[Path, Iterable[Tool]], None], ...] = (
    uv_project.require_project,
    node_project.require_project,
)
"""Each check that the repository holds the project its kind's tools run in."""


def require_projects(root: Path, tools: Collection[Tool]) -> None:
    """Require the repository at root to hold the project that each tool runs in.

    A kind whose tools run in a project adds its check to PROJECT_CHECKS.
    """
    for check in PROJECT_CHECKS:
        check(root, tools)


def load_definition(name: str, data: bytes, origin: Origin) -> ToolDefinition:
    """Parse one definition, reading its kind before any other field."""
    document = mapping(parse_toml(data), f"tools.{name}")
    kind = string(document.get("kind"), f"tools.{name}.kind")
    if kind not in KINDS:
        raise unsupported(f"tools.{name}.kind", kind)
    return parse_definition(name, origin, document, KINDS[kind])


def require_distinct(definitions: Iterable[ToolDefinition], subject: str) -> None:
    """Require distinct hook IDs, hook names and owned outputs across definitions.

    The Lock and run's results name hooks by ID, pre-commit's status lines by
    name, and each owned output has one writer. gateseal's own hooks claim
    their IDs and names first.
    subject names the definitions, such as the policy's tools, in the failure.
    """
    own = "gateseal's own hooks"
    claims = {("hook ID", hook): own for hook in OWN_HOOKS}
    claims |= {("hook name", name): own for name in OWN_HOOKS.values()}
    for definition in definitions:
        claimed = [("hook ID", hook.id) for hook in definition.hooks.values()]
        claimed += [("hook name", hook.name) for hook in definition.hooks.values()]
        claimed += [("output", path) for path in definition.owned]
        if isinstance(definition.source, pre_commit_repository.PreCommitRepository):
            claimed.append(("hook repository", definition.source.repository))
        for claim in claimed:
            owner = claims.setdefault(claim, definition.name)
            if owner != definition.name:
                raise GatesealError(
                    "duplicate_identifier",
                    f"{owner} and {definition.name} both claim the {claim[0]} "
                    f"{claim[1]!r}, but {subject} need distinct hook IDs, hook "
                    "names, outputs and hook repositories.",
                )


def load_directory(directory: Traversable) -> dict[str, ToolDefinition]:
    """Return each <name>.tool.toml in directory as a Built-in Tool, by name.

    A file without the suffix, or whose name is no tool name, is skipped.
    """
    definitions: dict[str, ToolDefinition] = {}
    for entry in sorted(directory.iterdir(), key=lambda entry: entry.name):
        name = entry.name.removesuffix(SUFFIX)
        if entry.name.endswith(SUFFIX) and NAME.fullmatch(name):
            definitions[name] = load_definition(
                name, entry.read_bytes(), Origin.BUILT_IN
            )
    require_distinct(definitions.values(), "built-in tools")
    return definitions


@functools.cache
def built_in_tools() -> Mapping[str, ToolDefinition]:
    """Return every definition in the package's data/tools, by name."""
    return load_directory(files("gateseal").joinpath("data", "tools"))
