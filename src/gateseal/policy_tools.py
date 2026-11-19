"""A policy's capabilities and the tools each one runs, as policy schema 5 says.

capabilities.<capability>.tools holds a table for each tool of the
capability, in the order a selection compiles them. The table says how the
policy uses the tool, its pin and its Reviewed Versions. A tool whose name is
a Built-in Tool's runs gateseal's definition. Any other name is a
Team-defined Tool, which the policy defines in tools/<name>.toml in the same
format. That file is a policy asset, so the content digest and every stored
release cover it, and the compiler parses it as data through its kind.

A capability's alternatives offer hooks of its tools for one job, such as
ruff-format or black for formatting. Each is a Policy Choice whose value is
the hook a repository runs; the others' hooks do not run. Its optional hooks
are hooks a repository may skip with the skip choice. A tool left without
hooks is not selected.
"""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import cast

from gateseal.diagnostics import GatesealError
from gateseal.tools import built_in_tools, load_definition, require_distinct
from gateseal.tools.choices import NAME as CHOICE
from gateseal.tools.configurations import FORMATS
from gateseal.tools.model import (
    NAME,
    Catalog,
    Hook,
    Origin,
    ToolDefinition,
    ToolUse,
)
from gateseal.tools.node import NAME as NODE
from gateseal.tools.node import package_name
from gateseal.validation import (
    fields,
    mapping,
    sequence,
    string,
    version,
)

CAPABILITY = re.compile(r"[a-z][a-z0-9-]*")
"""A capability's name, as policy.toml and a Manifest name it."""

TEAM_TOOLS = "tools/"
"""The directory of a policy's Team-defined Tools, one <name>.toml each."""

RULES = "rules/"
"""The directory of the tool configurations a policy copies into repositories."""

RUNTIME = ("pre-commit", "python")
"""The Compiler Runtime's parts, which exceptions and upgrades name like tools."""

EXCLUDE = "exclude"
"""The choice that keeps paths from the hooks of every tool of a capability."""

SKIP = "skip"
"""The choice of the optional hooks that a repository skips."""

RESERVED = frozenset({EXCLUDE, SKIP})
"""The choice names that gateseal itself declares in every capability."""

AssetReader = Callable[[str, str, str], bytes | None]
"""review_policy's reader of a file the policy names, which it keeps as an asset.

It takes the name, the field that names it and the file's extension, and
returns the bytes, or None when the policy has no such file.
"""


@dataclass(frozen=True)
class Alternatives:
    """A capability's choice of one hook among hooks of its tools, by hook ID.

    field is the policy.toml table that offers them.
    """

    field: str
    hooks: tuple[str, ...]

    def parse(self, value: object, field: str) -> str:
        """Return value, or fail with invalid_choice unless it is one of hooks."""
        if not isinstance(value, str) or value not in self.hooks:
            raise GatesealError(
                "invalid_choice",
                f"{field} takes one of {', '.join(self.hooks)}, not {value!r}.",
            )
        return value

    def dropped(self, value: str | tuple[str, ...]) -> frozenset[str]:
        """Return the hooks that do not run when value is chosen."""
        return frozenset(self.hooks) - {value}


@dataclass(frozen=True)
class OptionalHooks:
    """The hooks of a capability's tools that a repository may skip, by hook ID.

    The skip choice lists the ones it skips. field is the policy.toml field
    that marks them.
    """

    field: str
    hooks: tuple[str, ...]

    def parse(self, value: object, field: str) -> tuple[str, ...]:
        """Return value's distinct hooks, or fail with invalid_choice."""
        skipped = tuple(string(item, field) for item in sequence(value, field))
        for hook in skipped:
            if hook not in self.hooks:
                raise GatesealError(
                    "invalid_choice",
                    f"{field} names {hook}, which the policy does not mark as "
                    f"optional. It may skip {', '.join(self.hooks)}.",
                )
        if len(set(skipped)) != len(skipped):
            raise GatesealError("invalid_choice", f"{field} names a hook twice.")
        return skipped

    def dropped(self, value: str | tuple[str, ...]) -> frozenset[str]:
        """Return the hooks that value skips."""
        return frozenset(value)


HookChoice = Alternatives | OptionalHooks
"""A choice of which hooks of a capability run, by capability and choice name."""


@dataclass(frozen=True)
class Toolset:
    """What a policy's capabilities run.

    capabilities is in document order, the order a selection compiles in.
    definitions, uses and catalogs hold every tool of the capabilities, by
    name, in capability then tool order. hook_choices holds each capability's
    alternatives and optional hooks by capability and choice name, and
    defaults the value each capability's choices table gives each choice,
    still unchecked.
    """

    capabilities: tuple[str, ...]
    definitions: Mapping[str, ToolDefinition]
    uses: Mapping[str, ToolUse]
    catalogs: Mapping[str, Catalog]
    hook_choices: Mapping[tuple[str, str], HookChoice]
    defaults: Mapping[tuple[str, str], object]


def read_toolset(
    capabilities: object, read_asset: Callable[[str], bytes], asset: AssetReader
) -> Toolset:
    """Validate a policy's capabilities and the tools of each.

    A tool belongs to exactly one capability. The tools' hook IDs, hook names
    and owned outputs must be distinct.
    """
    tables: dict[str, tuple[str, dict[str, object]]] = {}
    hook_choices: dict[tuple[str, str], HookChoice] = {}
    defaults: dict[tuple[str, str], object] = {}
    entries = mapping(capabilities, "capabilities")
    if not entries:
        raise GatesealError("invalid_field", "The policy defines no capability.")
    for name, entry in entries.items():
        tools, offered, given = _capability(name, entry)
        hook_choices.update(offered)
        defaults.update(given)
        for tool, table in tools.items():
            if tables.setdefault(tool, (name, table))[0] != name:
                raise GatesealError(
                    "duplicate_identifier",
                    f"capabilities.{tables[tool][0]} and capabilities.{name} both "
                    f"have the tool {tool}; a tool belongs to exactly one "
                    "capability.",
                )
    definitions = {name: _definition(name, read_asset, asset) for name in tables}
    require_distinct(definitions.values(), "the policy's tools")
    known = set(built_in_tools()) | set(tables)
    uses: dict[str, ToolUse] = {}
    catalogs: dict[str, Catalog] = {}
    for name, (capability, table) in tables.items():
        field = f"capabilities.{capability}.tools.{name}"
        table = _tool_fields(definitions[name], table, field)
        catalogs[name] = _catalog(name, table, field, known)
        uses[name] = _use(
            capability, definitions[name], table, field, asset, catalogs[name].packages
        )
    _require_hook_choices(hook_choices, uses)
    return Toolset(tuple(entries), definitions, uses, catalogs, hook_choices, defaults)


def _capability(
    name: str, entry: object
) -> tuple[
    dict[str, dict[str, object]],
    dict[tuple[str, str], HookChoice],
    dict[tuple[str, str], object],
]:
    """Validate capabilities.<name>: its tools, hook choices and choices."""
    field = f"capabilities.{name}"
    table = mapping(entry, field)
    names = {"tools"} | ({"alternatives", "optional", "choices"} & set(table))
    table = fields(table, names, field)
    tools = {
        tool: mapping(value, f"{field}.tools.{tool}")
        for tool, value in mapping(table["tools"], f"{field}.tools").items()
    }
    if not CAPABILITY.fullmatch(name) or not tools:
        raise GatesealError(
            "invalid_field",
            f"{field} needs a name of lowercase letters, digits and -, and at "
            "least one tool.",
        )
    for tool in tools:
        if not NAME.fullmatch(tool):
            raise GatesealError(
                "invalid_field",
                f"{field}.tools.{tool} is not a tool name; a tool's name is "
                "lowercase letters, digits and -.",
            )
    offered: dict[tuple[str, str], HookChoice] = {
        (name, choice): _alternatives(name, choice, hooks)
        for choice, hooks in mapping(
            table.get("alternatives", {}), f"{field}.alternatives"
        ).items()
    }
    if "optional" in table:
        offered[name, SKIP] = _optional(f"{field}.optional", table["optional"])
    defaults = {
        (name, choice): default
        for choice, default in mapping(
            table.get("choices", {}), f"{field}.choices"
        ).items()
    }
    return tools, offered, defaults


def _alternatives(capability: str, name: str, value: object) -> Alternatives:
    """Validate capabilities.<capability>.alternatives.<name>: two or more hooks."""
    field = f"capabilities.{capability}.alternatives.{name}"
    if name in RESERVED:
        raise GatesealError(
            "invalid_field",
            f"{field} declares the choice {name}, which gateseal itself declares.",
        )
    hooks = tuple(string(item, field) for item in sequence(value, field))
    if not CHOICE.fullmatch(name) or len(hooks) < 2 or len(set(hooks)) != len(hooks):
        raise GatesealError(
            "invalid_field",
            f"{field} needs a lowercase name and two or more distinct hook IDs.",
        )
    return Alternatives(field, hooks)


def _optional(field: str, value: object) -> OptionalHooks:
    """Validate capabilities.<capability>.optional: one or more hooks."""
    hooks = tuple(string(item, field) for item in sequence(value, field))
    if not hooks or len(set(hooks)) != len(hooks):
        raise GatesealError(
            "invalid_field", f"{field} needs one or more distinct hook IDs."
        )
    return OptionalHooks(field, hooks)


def _require_hook_choices(
    hook_choices: Mapping[tuple[str, str], HookChoice], uses: Mapping[str, ToolUse]
) -> None:
    """Require each hook choice to offer hooks its capability runs, each in one."""
    runs = {hook.id: use.capability for use in uses.values() for hook in use.hooks}
    offered: dict[str, HookChoice] = {}
    for (capability, _), choice in hook_choices.items():
        for hook in choice.hooks:
            if runs.get(hook) != capability:
                raise GatesealError(
                    "unsupported_hook",
                    f"{choice.field} offers {hook}, which no tool of {capability} "
                    "runs.",
                )
            if offered.setdefault(hook, choice) is not choice:
                raise GatesealError(
                    "duplicate_identifier",
                    f"{choice.field} offers {hook}, which {offered[hook].field} "
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
            next_step="Give the tool another name in its capability's tools.",
        )
    if name == NODE:
        raise GatesealError(
            "reserved_tool_name",
            f"The policy names the tool {NODE}, but {NODE} names the Node that "
            f"{NODE} in policy.toml pins.",
            next_step="Give the tool another name in its capability's tools.",
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
            f"{TEAM_TOOLS}<new name>.toml, give its table in its capability's "
            f"tools that name, and delete {path}.",
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


def _tool_fields(
    definition: ToolDefinition, table: dict[str, object], field: str
) -> dict[str, object]:
    """Require a tool's table to hold exactly the fields its definition allows.

    pin and reviewed are required, hook_ids and requires optional, and
    configuration is required exactly when the definition declares one.
    packages is optional for a tool on Node and refused for any other.
    """
    if "packages" in table and not definition.source.on_node:
        raise GatesealError(
            "invalid_field",
            f"{field}.packages needs a tool that runs on Node, but "
            f"{definition.name} is a {definition.source.kind} tool.",
        )
    names = {"pin", "reviewed"} | ({"hook_ids", "requires", "packages"} & set(table))
    if definition.configuration is not None:
        names.add("configuration")
    return fields(table, names, field)


def _use(
    capability: str,
    definition: ToolDefinition,
    table: dict[str, object],
    field: str,
    asset: AssetReader,
    packages: Mapping[str, str],
) -> ToolUse:
    """Return the hooks the policy runs and its configuration of one tool.

    Without hook_ids the policy runs every hook of the definition, in its
    order. configuration names a file under rules/ exactly when the
    definition declares a configuration, and the definition's format checks
    it against packages, the tool's packages that policy.toml pins. A tool
    that does not run on Node has none, so its format gets None.
    """
    hooks = tuple(definition.hooks.values())
    if "hook_ids" in table:
        hooks = _hooks(table["hook_ids"], definition, f"{field}.hook_ids")
    configuration = None
    if definition.configuration is not None:
        path = string(table["configuration"], f"{field}.configuration")
        if not path.startswith(RULES):
            raise GatesealError(
                "invalid_field",
                f"{field}.configuration must name a file under {RULES}.",
            )
        rules = FORMATS[definition.configuration.format]
        configuration = asset(path, f"{field}.configuration", rules.source)
        if configuration is None:
            raise GatesealError(
                "missing_policy_asset", f"The policy has no file {path}."
            )
        rules.check(
            configuration,
            f"The {definition.name} configuration",
            packages if definition.source.on_node else None,
            f"{field}.packages",
        )
    return ToolUse(capability, hooks, configuration)


def _catalog(
    name: str, table: dict[str, object], field: str, known: set[str]
) -> Catalog:
    """Return a tool's pin and Reviewed Versions, with what each version requires.

    reviewed lists stable releases, each once, and pin is one of them. requires
    maps a reviewed version to the minimum version of each tool it needs,
    which is a Built-in Tool or one of the policy's, and never this one.
    packages maps each npm package to its exact stable release, by name.
    """
    listed = f"{field}.reviewed"
    reviewed = tuple(
        version(item, listed) for item in sequence(table["reviewed"], listed)
    )
    if not reviewed or len(set(reviewed)) != len(reviewed):
        raise GatesealError(
            "invalid_field", f"{listed} must list each reviewed version once."
        )
    requires: dict[str, dict[str, str]] = {}
    for key, value in mapping(table.get("requires", {}), f"{field}.requires").items():
        entry = f"{field}.requires.{key}"
        if key not in reviewed:
            raise GatesealError(
                "invalid_field",
                f"{entry} names {key}, which {listed} does not list.",
            )
        needs = mapping(value, entry)
        for other, minimum in needs.items():
            if other not in known or other == name:
                raise GatesealError(
                    "invalid_field",
                    f"{entry} requires {other!r}, which is not another tool of "
                    "the policy or a Built-in Tool.",
                )
            version(minimum, f"{entry}.{other}")
        requires[key] = cast(dict[str, str], needs)
    pin = string(table["pin"], f"{field}.pin")
    if pin not in reviewed:
        raise GatesealError("invalid_field", f"{field}.pin {pin} is not in {listed}.")
    pinned = f"{field}.packages"
    packages = {
        package_name(key, pinned): version(value, f"{pinned}.{key}")
        for key, value in sorted(mapping(table.get("packages", {}), pinned).items())
    }
    return Catalog(pin, {key: requires.get(key, {}) for key in reviewed}, packages)


def _hooks(value: object, definition: ToolDefinition, field: str) -> tuple[Hook, ...]:
    """Validate the hooks of its definition that a tool runs, in their order."""
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
