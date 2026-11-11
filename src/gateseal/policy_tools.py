"""A policy's capabilities and the tools each one runs, as policy schema 4 says.

[capabilities] names each capability's tools, in the order a selection
compiles them, and [tools] says how the policy uses each one. A tool whose
name is a Built-in Tool's runs gateseal's definition. Any other name is a
Team-defined Tool, which the policy defines in tools/<name>.toml in the same
format. That file is a policy asset, so the content digest and every stored
release cover it, and the compiler parses it as data through its kind.

A capability's alternatives offer hooks of its tools for one job, such as
ruff-format or black for formatting. Each is a Policy Choice whose value is
the hook a repository runs; the others' hooks do not run, and a tool left
without hooks is not selected.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from gateseal.diagnostics import GatesealError
from gateseal.tools import built_in_tools, load_definition, require_distinct
from gateseal.tools.choices import EXTEND
from gateseal.tools.choices import NAME as CHOICE
from gateseal.tools.model import NAME, Hook, Origin, ToolDefinition, ToolUse
from gateseal.validation import fields, mapping, parse_toml, sequence, string

CAPABILITY = re.compile(r"[a-z][a-z0-9-]*")
"""A capability's name, as policy.toml and a Manifest name it."""

HYGIENE = "hygiene"
"""The capability every policy defines, since Manifest schema 1 selects it."""

TEAM_TOOLS = "tools/"
"""The directory of a policy's Team-defined Tools, one <name>.toml each."""

RULES = "rules/"
"""The directory of the tool configurations a policy copies into repositories."""

RUNTIME = ("pre-commit", "python")
"""The Compiler Runtime's parts, which exceptions and upgrades name like tools."""

AssetReader = Callable[[str, str, str], bytes | None]
"""review_policy's reader of a file the policy names, which it keeps as an asset.

It takes the name, the field that names it and the file's extension, and
returns the bytes, or None when the policy has no such file.
"""


@dataclass(frozen=True)
class Alternatives:
    """A capability's choice of one hook among hooks of its tools, by hook ID."""

    hooks: tuple[str, ...]

    def parse(self, value: object, field: str) -> str:
        """Return value, or fail with invalid_choice unless it is one of hooks."""
        if not isinstance(value, str) or value not in self.hooks:
            raise GatesealError(
                "invalid_choice",
                f"{field} takes one of {', '.join(self.hooks)}, not {value!r}.",
            )
        return value


@dataclass(frozen=True)
class Toolset:
    """What a policy's capabilities run.

    capabilities is in document order, the order a selection compiles in.
    definitions and uses hold every tool the capabilities name, by name, in
    capability then tool order. alternatives holds each capability's
    alternatives by capability and choice name.
    """

    capabilities: tuple[str, ...]
    definitions: Mapping[str, ToolDefinition]
    uses: Mapping[str, ToolUse]
    alternatives: Mapping[tuple[str, str], Alternatives]


def validate_configuration(data: bytes, field: str) -> None:
    """Check a native configuration, which configure copies into an owned output.

    Owned outputs are compared with line endings normalized to LF, so a file
    with a carriage return could never match its own output. A configuration
    that loads another file would escape review.
    """
    if b"\r" in data:
        raise GatesealError(
            "invalid_field", f"{field} must use LF line endings, without CR."
        )
    if EXTEND in parse_toml(data):
        raise GatesealError(
            "invalid_field", f"{field} must not extend another configuration."
        )


def read_toolset(
    capabilities: object,
    tools: object,
    read_asset: Callable[[str], bytes],
    asset: AssetReader,
) -> Toolset:
    """Validate a policy's capabilities and tools tables.

    tools holds a table for exactly the tools the capabilities name. Their
    hook IDs, hook names and owned outputs must be distinct.
    """
    offered, alternatives = _capabilities(capabilities)
    owner = {tool: name for name, listed in offered.items() for tool in listed}
    tables = mapping(tools, "tools")
    for name in tables:
        if name not in owner:
            raise GatesealError(
                "invalid_field",
                f"tools.{name} configures a tool that no capability names.",
            )
    for name in owner:
        if name not in tables:
            raise GatesealError(
                "invalid_field",
                f"capabilities.{owner[name]} names {name}, which needs a "
                f"tools.{name} table.",
            )
    definitions = {name: _definition(name, read_asset, asset) for name in owner}
    require_distinct(definitions.values(), "the policy's tools")
    uses = {
        name: _use(owner[name], definitions[name], tables[name], asset)
        for name in owner
    }
    _require_alternatives(alternatives, uses)
    return Toolset(tuple(offered), definitions, uses, alternatives)


def _capabilities(
    value: object,
) -> tuple[dict[str, tuple[str, ...]], dict[tuple[str, str], Alternatives]]:
    """Return each capability's tools and alternatives.

    A tool belongs to exactly one capability.
    """
    offered: dict[str, tuple[str, ...]] = {}
    alternatives: dict[tuple[str, str], Alternatives] = {}
    owner: dict[str, str] = {}
    for name, entry in mapping(value, "capabilities").items():
        tools, choices = _capability(name, entry)
        alternatives.update(choices)
        for tool in tools:
            if not NAME.fullmatch(tool):
                raise GatesealError(
                    "invalid_field",
                    f"capabilities.{name}.tools names {tool!r}; a tool's name is "
                    "lowercase letters, digits and -.",
                )
            if owner.setdefault(tool, name) != name or tools.count(tool) > 1:
                raise GatesealError(
                    "duplicate_identifier",
                    f"capabilities.{owner[tool]} and capabilities.{name} both name "
                    f"{tool}; a tool belongs to exactly one capability, once.",
                )
        offered[name] = tools
    if HYGIENE not in offered:
        raise GatesealError(
            "invalid_field",
            f"The policy defines no {HYGIENE} capability, which every Manifest "
            "selects.",
        )
    return offered, alternatives


def _capability(
    name: str, entry: object
) -> tuple[tuple[str, ...], dict[tuple[str, str], Alternatives]]:
    """Validate capabilities.<name>: its tools and its alternatives."""
    field = f"capabilities.{name}.tools"
    table = mapping(entry, f"capabilities.{name}")
    table = fields(
        table, {"tools"} | ({"alternatives"} & set(table)), f"capabilities.{name}"
    )
    listed = table["tools"]
    tools = tuple(string(item, field) for item in sequence(listed, field))
    alternatives: dict[tuple[str, str], Alternatives] = {}
    for choice, hooks in mapping(
        table.get("alternatives", {}), f"capabilities.{name}.alternatives"
    ).items():
        alternatives[name, choice] = _alternatives(name, choice, hooks)
    if not CAPABILITY.fullmatch(name) or not tools:
        raise GatesealError(
            "invalid_field",
            f"capabilities.{name} needs a name of lowercase letters, digits "
            "and -, and at least one tool.",
        )
    return tools, alternatives


def _alternatives(capability: str, name: str, value: object) -> Alternatives:
    """Validate capabilities.<capability>.alternatives.<name>: two or more hooks."""
    field = f"capabilities.{capability}.alternatives.{name}"
    hooks = tuple(string(item, field) for item in sequence(value, field))
    if not CHOICE.fullmatch(name) or len(hooks) < 2 or len(set(hooks)) != len(hooks):
        raise GatesealError(
            "invalid_field",
            f"{field} needs a lowercase name and two or more distinct hook IDs.",
        )
    return Alternatives(hooks)


def _require_alternatives(
    alternatives: Mapping[tuple[str, str], Alternatives], uses: Mapping[str, ToolUse]
) -> None:
    """Require each alternative to be a hook its capability runs, in one choice."""
    runs = {hook.id: use.capability for use in uses.values() for hook in use.hooks}
    offered: dict[str, str] = {}
    for (capability, name), choice in alternatives.items():
        field = f"capabilities.{capability}.alternatives.{name}"
        for hook in choice.hooks:
            if runs.get(hook) != capability:
                raise GatesealError(
                    "unsupported_hook",
                    f"{field} offers {hook}, which no tool of {capability} runs.",
                )
            if offered.setdefault(hook, name) != name:
                raise GatesealError(
                    "duplicate_identifier",
                    f"{field} offers {hook}, which "
                    f"capabilities.{capability}.alternatives.{offered[hook]} "
                    "offers too.",
                )


def _definition(
    name: str, read_asset: Callable[[str], bytes], asset: AssetReader
) -> ToolDefinition:
    """Return the Built-in Tool name, or the policy's Team-defined Tool name.

    A built-in's name is reserved, so the policy cannot also define it. An
    error in a team definition names its file.
    """
    path = f"{TEAM_TOOLS}{name}.toml"
    if name in RUNTIME:
        raise GatesealError(
            "reserved_tool_name",
            f"The policy names the tool {name}, but {' and '.join(RUNTIME)} name "
            "the Compiler Runtime that each gateseal release carries.",
            next_step="Name the tool something else in its capability and tools "
            "tables.",
        )
    built_in = built_in_tools().get(name)
    if built_in is not None:
        try:
            read_asset(path)
        except FileNotFoundError:
            return built_in
        raise GatesealError(
            "reserved_tool_name",
            f"{path} defines {name}, but {name} is a Built-in Tool, so its name "
            "always means gateseal's definition.",
            next_step=f"To run {name} another way, copy its definition to "
            f"{TEAM_TOOLS}<new name>.toml, name that tool in its capability "
            f"and tools tables, and delete {path}.",
        )
    data = asset(path, f"tools.{name}", ".toml")
    if data is None:
        raise GatesealError(
            "unknown_tool",
            f"The policy names the tool {name}, but gateseal has no built-in tool "
            f"{name} and the policy has no {path}.",
            next_step=f"Define {name} in {path}, or name one of the built-in "
            f"tools {', '.join(built_in_tools())}.",
        )
    try:
        return load_definition(name, data, Origin.TEAM)
    except GatesealError as exc:
        step = exc.next_step
        if exc.code == "unsupported_tool_definition":
            step += (
                " Raise minimum_compiler in policy.toml to that compiler, so an "
                "older one stops at it."
            )
        raise GatesealError(exc.code, f"{path}: {exc}", exc.exit_code, step) from exc


def _use(
    capability: str, definition: ToolDefinition, value: object, asset: AssetReader
) -> ToolUse:
    """Validate tools.<name>: the hooks the policy runs and its configuration.

    Without hook_ids the policy runs every hook of the definition, in its
    order. configuration names a file under rules/ exactly when the
    definition declares a configuration.
    """
    name = definition.name
    field = f"tools.{name}"
    table = mapping(value, field)
    names = {"hook_ids"} & set(table)
    if definition.configuration is not None:
        names.add("configuration")
    table = fields(table, names, field)
    hooks = tuple(definition.hooks.values())
    if "hook_ids" in table:
        hooks = _hooks(table["hook_ids"], definition)
    configuration = None
    if definition.configuration is not None:
        path = string(table["configuration"], f"{field}.configuration")
        if not path.startswith(RULES):
            raise GatesealError(
                "invalid_field",
                f"{field}.configuration must name a file under {RULES}.",
            )
        configuration = asset(
            path,
            f"{field}.configuration",
            PurePosixPath(definition.configuration).suffix,
        )
        if configuration is None:
            raise GatesealError(
                "missing_policy_asset", f"The policy has no file {path}."
            )
        validate_configuration(configuration, f"The {name} configuration")
    return ToolUse(capability, hooks, configuration)


def _hooks(value: object, definition: ToolDefinition) -> tuple[Hook, ...]:
    """Validate the hooks of its definition that a tool runs, in their order."""
    field = f"tools.{definition.name}.hook_ids"
    names = tuple(string(v, field) for v in sequence(value, field))
    if not names or set(names) - set(definition.hooks):
        raise GatesealError(
            "unsupported_hook",
            f"{field} must name some of {', '.join(definition.hooks)}, or be "
            "left out to run them all.",
        )
    if len(set(names)) != len(names):
        raise GatesealError("duplicate_identifier", f"{field} names a hook twice.")
    return tuple(definition.hooks[name] for name in names)
