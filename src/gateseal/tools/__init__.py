"""The Tool Kinds this compiler implements, and its Built-in Tools.

Each kind is a module in this package, and KINDS maps its name to the parser of
its [source] table. A Built-in Tool is a Tool Definition in the package's
data/tools, <name>.tool.toml, so the gateseal commit that the Lock pins
covers it. The suffix keeps every tool from reading a definition as its own
configuration, as Ruff reads any ruff.toml it walks past. Kind modules import
gateseal.tools.model, never this package, so loading it cannot cycle.
"""

import functools
from collections.abc import Callable, Iterable, Mapping
from importlib.resources import files
from importlib.resources.abc import Traversable

from gateseal.diagnostics import GatesealError
from gateseal.tools import pre_commit_repository, release_binary, uv_project
from gateseal.tools.model import (
    NAME,
    Origin,
    Source,
    ToolDefinition,
    parse_definition,
    unsupported,
)
from gateseal.validation import mapping, parse_toml, string

SUFFIX = ".tool.toml"

KINDS: Mapping[str, Callable[[object, str], Source]] = {
    pre_commit_repository.PreCommitRepository.kind: pre_commit_repository.parse_source,
    release_binary.ReleaseBinary.kind: release_binary.parse_source,
    uv_project.UvProject.kind: uv_project.parse_source,
}
"""Each kind's parser of a definition's [source], by its value of kind."""


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
    name, and each owned output has one writer.
    subject names the definitions, such as the policy's tools, in the failure.
    """
    claims: dict[tuple[str, str], str] = {}
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
