"""Tool Definitions, and what every Tool Kind shares.

A Tool Definition is data that parameterizes one Tool Kind. Each kind's module
owns its [source] table, its policy release fields, its rendering, its Lock
record and its preparation. This module holds what every kind shares, so no
kind renders a hook argument, an owned output or a local hook its own way.

Every placeholder is resolved, and every closed value checked, when a
definition is parsed. Code that holds a ToolDefinition trusts it.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Literal, Protocol

from gateseal import __version__
from gateseal.bootstrap import TOOL_PREFIX
from gateseal.diagnostics import GatesealError
from gateseal.tools.choices import Choice, parse_choices
from gateseal.validation import fields, mapping, sequence, string, version_key

OWNED = ".gateseal"
"""The directory of every owned output except the native configuration."""

LOCAL_MINIMUM_PRE_COMMIT = "4.4.0"
"""The oldest pre-commit that runs a hook as local_repository renders it.

Its language, unsupported, came with pre-commit 4.4.0.
"""

NAME = re.compile(r"[a-z][a-z0-9-]*")
"""A tool's, hook's or file's name, as owned paths and prepared links use it."""

PROGRAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""An executable's bare name, which its kind resolves to one it prepared."""

PREFIX = re.compile(r"[A-Z][A-Z0-9]*_")

KEPT = ("GIT_DIR", "GIT_INDEX_FILE")
"""The variables a commit passes to pre-commit to name the index it records,
which no removed prefix may drop."""

RANGE = "{range}"
"""The placeholder a history scan's arguments hold once for the gate's range."""


class Stage(StrEnum):
    COMMIT = "commit"
    RUN = "run"
    CI = "ci"


HOOK_STAGES: Mapping[Stage, str] = {
    Stage.COMMIT: "pre-commit",
    Stage.RUN: "pre-commit",
    Stage.CI: "manual",
}
"""The pre-commit stage that runs each Stage's hooks, as --hook-stage names it.

A built-in hook has no stages key, so pre-commit runs it in every stage.
"""


class Scope(StrEnum):
    FILES = "files"


class OutputFormat(StrEnum):
    """How a hook reports.

    CONCISE prints each finding as file:line:column: rule message; PLAIN
    reports only that the hook passed, failed or modified files.
    """

    CONCISE = "concise"
    PLAIN = "plain"


class ConfigurationFormat(StrEnum):
    TOML = "toml"


class Origin(StrEnum):
    """Who wrote a definition.

    gateseal ships a BUILT_IN one in its package. A policy's tools/ folder holds
    a TEAM one, which carries its kind's guarantees but the tool-specific ones,
    such as turning off a scanner's ignore file, only as far as its arguments
    declare them.
    """

    BUILT_IN = "built-in"
    TEAM = "team-defined"


LockSection = Literal["components", "native_artifacts"]
"""The Lock's sections of tool records, which the Lock schema fixes."""


def unsupported(field: str, value: str) -> GatesealError:
    """Return the failure of a closed value this compiler does not implement.

    The next step names a newer compiler, as an unknown policy schema does.
    """
    return GatesealError(
        "unsupported_tool_definition",
        f"{field} is {value!r}, which gateseal {__version__} does not implement.",
        next_step="Use a gateseal compiler that implements this tool definition.",
    )


def lock_failure(code: str, message: str) -> GatesealError:
    """Return the refusal of a Policy Lock that disagrees with policy.toml."""
    return GatesealError(
        code, message, next_step="Run gateseal policy lock in the policy directory."
    )


class MissingUpstream(GatesealError):
    """The refusal of a reviewed version whose upstream lacks a fact.

    name and version say which version, so gateseal policy add can tell its
    new version's refusal from a reviewed one's.
    """

    def __init__(self, code: str, message: str, name: str, version: str) -> None:
        super().__init__(
            code,
            message,
            next_step=f"Check that {name} {version} is released, or remove it "
            "from policy.toml. Then run gateseal policy lock.",
        )
        self.name = name
        self.version = version


def changed_upstream(
    name: str, version: str, source: str, recorded: object, found: object
) -> GatesealError:
    """Return the refusal of a recorded fact that upstream no longer gives.

    gateseal policy lock never rewrites a recorded fact. Accepting the new one
    takes a new lock, whose diff shows every fact that changed.
    """
    return GatesealError(
        "upstream_changed",
        f"policy.lock records {json.dumps(recorded, sort_keys=True)} for {name} "
        f"{version}, but {source} now gives {json.dumps(found, sort_keys=True)}.",
        next_step="Find out why upstream changed. To accept it, delete "
        "policy.lock, run gateseal policy lock, and review every change in the "
        "diff of policy.lock.",
    )


def closed[E: StrEnum](value: object, members: type[E], field: str) -> E:
    """Return value as a member of a closed set, or fail through unsupported."""
    text = string(value, field)
    try:
        return members(text)
    except ValueError:
        raise unsupported(field, text) from None


@dataclass(frozen=True)
class Hook:
    """One hook of a tool, with every placeholder resolved.

    tool is the definition's name, and origin its definition's. id and name
    are unique among the hooks of a policy's tools: the Lock and run's results
    name a hook by its id, and pre-commit's status line by its name. args
    follow the program the kind runs, never through a shell. probe runs
    without file names from the worktree's top on empty standard input; its
    first word is a bare executable name that the kind resolves to an
    executable it prepared, never to a host program.
    """

    tool: str
    origin: Origin
    id: str
    name: str
    args: tuple[str, ...]
    types_or: tuple[str, ...]
    probe: tuple[str, ...]
    output: OutputFormat

    def base_entry(self, exclude: str | None) -> dict[str, object]:
        """Return the native configuration keys every rendering of the hook shares."""
        entry: dict[str, object] = {"id": self.id, "name": self.name}
        if self.types_or:
            entry["types_or"] = list(self.types_or)
        if exclude is not None:
            entry["exclude"] = exclude
        return entry


@dataclass(frozen=True)
class Executable:
    r"""The executable a kind prepares for a tool, and how it proves its version.

    version_output is a regular expression that the stripped output of the
    executable run with the version arguments must fully match. {version}
    stands for the escaped selected version between (?<![0-9.]) and
    (?![\w.+-]), so even .*{version}.* refuses 11.9.0, 1.9.0.1 and 1.9.0-rc1
    for 1.9.0. The text on each side of {version} is a regular expression of
    its own, in its own group, so no alternation, optional group, or repeat can
    skip the version, and a definition cannot loosen the proof.
    """

    name: str
    version: tuple[str, ...]
    version_output: str

    def require_version(self, reported: str, version: str, subject: str) -> None:
        """Fail with tool_failed unless reported proves version."""
        if (
            re.fullmatch(_version_pattern(self.version_output, version), reported)
            is None
        ):
            raise GatesealError(
                "tool_failed",
                f"Prepared {subject} reports version {reported}, not {version}.",
                4,
                "gateseal activate",
            )


@dataclass(frozen=True)
class Release:
    """One reviewed version of a tool. Each kind adds its Policy Lock facts.

    requires names the oldest version of each other tool that this one works
    with.
    """

    version: str
    requires: Mapping[str, str]


@dataclass(frozen=True)
class Catalog:
    """A component as policy.toml lists it: its pin and its reviewed versions.

    requires holds what each reviewed version requires, by version; pin is
    one of them.
    """

    pin: str
    requires: Mapping[str, Mapping[str, str]]


@dataclass(frozen=True)
class ToolUse:
    """How a policy uses one tool.

    capability names what selects it. hooks are the definition's hooks the
    policy runs, in configuration order. configuration is the policy's file
    that configure copies into the tool's owned configuration output; the
    policy sets it exactly when the definition declares one.
    """

    capability: str
    hooks: tuple[Hook, ...]
    configuration: bytes | None


class Upstream(Protocol):
    """Where gateseal policy lock and add read facts. No other command reaches it."""

    def tagged(self, repository: str, tag: str) -> str | None:
        """Return the commit that tag names, peeling an annotated tag, or None."""
        ...

    def download(self, url: str) -> bytes | None:
        """Return a file's bytes, or None when the server has no such file."""
        ...


class Source(Protocol):
    """A definition's [source] table as its kind parsed it, and the kind's rules.

    kind is the kind's value of the definition's kind field. local says that
    the kind's hooks run the prepared executable through the attempt's tools
    link, as local_repository renders them. python says that the kind's hooks
    run on a Python interpreter, which a policy's Hook Python replaces.
    """

    kind: ClassVar[str]
    local: ClassVar[bool]
    python: ClassVar[bool]

    def offer(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        records: Mapping[str, object],
    ) -> Tool:
        """Return the tool at its pin, each reviewed version with its facts.

        records holds each reviewed version's Policy Lock record, by version.
        A field it finds invalid fails with invalid_field.
        """
        ...

    def lock(
        self,
        definition: ToolDefinition,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Return a reviewed version's Policy Lock record, resolved from upstream.

        recorded is the record the lock holds for the version, or None. Each
        fact it records must equal upstream's, or the version fails with
        upstream_changed. Only a fact it lacks is resolved anew and checked.
        """
        ...


@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    """One tool as data: the fields every kind shares, and its kind's source.

    name comes from the file name, and origin from where the file lives.
    configuration is the owned output that the policy's configuration goes
    to, .gateseal/<name>.<format>; files maps each fixed owned output,
    .gateseal/<name>-<key>.<extension>, to its bytes. Both are derived from
    the name, so a definition cannot name a path the compiler does not own.
    stages and scope are closed values that no rendering reads yet, because
    this compiler renders every tool into every stage and implements one
    scope. history holds the arguments of the prepared
    executable that scan a revision range, one of which holds RANGE, where
    the CI gate puts the range; only a kind whose hooks are local has one.
    choices holds the Policy Choices the definition declares, by name; only a
    definition with a configuration declares any, since each renders into it.
    """

    name: str
    origin: Origin
    stages: frozenset[Stage]
    scope: Scope
    removed_prefixes: tuple[str, ...]
    configuration: str | None
    files: Mapping[str, bytes]
    executable: Executable | None
    hooks: Mapping[str, Hook]
    source: Source
    history: tuple[str, ...] | None
    choices: Mapping[str, Choice]

    @property
    def owned(self) -> tuple[str, ...]:
        """Return every owned output the definition claims."""
        configuration = () if self.configuration is None else (self.configuration,)
        return (*configuration, *self.files)


def _arguments(
    value: object, field: str, owned: Mapping[str, str], kept: str = ""
) -> tuple[str, ...]:
    """Validate literal arguments, resolving each exact {<owned key>} to its path.

    Any other brace fails, so an argument reaches an owned output only through
    its exact placeholder. kept names a placeholder that stays in place, such
    as RANGE, which the gate resolves. Other literals, such as Betterleaks'
    /dev/null, pass to the tool unchanged.
    """
    arguments: list[str] = []
    for item in sequence(value, field):
        text = string(item, field)
        rest = text.replace(kept, "") if kept else text
        if text.startswith("{") and text.endswith("}") and text[1:-1] in owned:
            text = owned[text[1:-1]]
        elif "{" in rest or "}" in rest:
            raise GatesealError(
                "invalid_field",
                f"{field} holds {text!r}; an argument names an owned file only as "
                "exactly {configuration} or {<files key>}.",
            )
        arguments.append(text)
    return tuple(arguments)


def _program(value: str, field: str) -> str:
    if not PROGRAM.fullmatch(value):
        raise GatesealError(
            "invalid_field", f"{field} must be a bare executable name, not {value!r}."
        )
    return value


def _version_pattern(output: str, version: str) -> str:
    before, after = output.split("{version}")
    bounded = rf"(?<![0-9.]){re.escape(version)}(?![\w.+-])"
    return f"(?:{before}){bounded}(?:{after})"


def _executable(value: object, field: str, owned: Mapping[str, str]) -> Executable:
    table = fields(value, {"name", "version", "version_output"}, field)
    output = string(table["version_output"], f"{field}.version_output")
    try:
        valid = output.count("{version}") == 1 and all(
            re.compile(part)
            for part in (*output.split("{version}"), _version_pattern(output, "0"))
        )
    except re.error:
        valid = False
    if not valid:
        raise GatesealError(
            "invalid_field",
            f"{field}.version_output must hold {{version}} once, between two "
            "regular expressions that each compile on their own.",
        )
    return Executable(
        _program(string(table["name"], f"{field}.name"), f"{field}.name"),
        _arguments(table["version"], f"{field}.version", owned),
        output,
    )


def _files(name: str, value: object, field: str) -> dict[str, tuple[str, bytes]]:
    """Return each fixed file by key, with its derived owned path and its bytes."""
    found: dict[str, tuple[str, bytes]] = {}
    for key, entry in mapping(value, field).items():
        table = fields(entry, {"extension", "content"}, f"{field}.{key}")
        extension = string(table["extension"], f"{field}.{key}.extension")
        content = table["content"]
        if (
            not NAME.fullmatch(key)
            or key == "configuration"
            or not re.fullmatch("[a-z0-9]+", extension)
            or not isinstance(content, str)
            or "\r" in content
        ):
            raise GatesealError(
                "invalid_field",
                f"{field}.{key} needs a lowercase name other than configuration, "
                "a lowercase extension and text content with LF line endings.",
            )
        found[key] = (f"{OWNED}/{name}-{key}.{extension}", content.encode())
    return found


def _hook(
    tool: str, origin: Origin, hook_id: str, value: object, owned: Mapping[str, str]
) -> Hook:
    field = f"tools.{tool}.hooks.{hook_id}"
    table = mapping(value, field)
    output = closed(table.get("output"), OutputFormat, f"{field}.output")
    table = fields(
        table, {"name", "probe", "output"} | ({"args", "types_or"} & set(table)), field
    )
    probe = _arguments(table["probe"], f"{field}.probe", owned)
    if not NAME.fullmatch(hook_id) or not probe:
        raise GatesealError(
            "invalid_field", f"{field} needs a lowercase id and a nonempty probe."
        )
    _program(probe[0], f"{field}.probe")
    return Hook(
        tool,
        origin,
        hook_id,
        string(table["name"], f"{field}.name"),
        _arguments(table.get("args", []), f"{field}.args", owned),
        tuple(
            string(item, f"{field}.types_or")
            for item in sequence(table.get("types_or", []), f"{field}.types_or")
        ),
        probe,
        output,
    )


def _stages(value: object, field: str) -> frozenset[Stage]:
    listed = [closed(item, Stage, field) for item in sequence(value, field)]
    if set(listed) != set(Stage) or len(set(listed)) != len(listed):
        raise unsupported(field, ", ".join(listed))
    return frozenset(listed)


def _removed_prefixes(value: object, field: str) -> tuple[str, ...]:
    prefixes = tuple(string(item, field) for item in sequence(value, field))
    if (
        len(set(prefixes)) != len(prefixes)
        or not all(map(PREFIX.fullmatch, prefixes))
        # pytestmut: off[call.anyall] -- only GIT_ fits PREFIX and starts either name
        or any(variable.startswith(prefixes) for variable in KEPT)
        # pytestmut: on
    ):
        raise GatesealError(
            "invalid_field",
            f"{field} must be distinct prefixes such as RUFF_, "
            f"and none may remove {' or '.join(KEPT)}.",
        )
    return prefixes


def _hooks(
    tool: str, origin: Origin, value: object, owned: Mapping[str, str]
) -> dict[str, Hook]:
    field = f"tools.{tool}.hooks"
    hooks = {
        hook_id: _hook(tool, origin, hook_id, item, owned)
        for hook_id, item in mapping(value, field).items()
    }
    if not hooks or len({hook.name for hook in hooks.values()}) != len(hooks):
        raise GatesealError(
            "invalid_field", f"{field} must name at least one hook, each once."
        )
    return hooks


def _history(
    value: object, field: str, owned: Mapping[str, str], source: Source
) -> tuple[str, ...]:
    table = fields(value, {"args"}, field)
    history = _arguments(table["args"], f"{field}.args", owned, RANGE)
    if not source.local or sum(arg.count(RANGE) for arg in history) != 1:
        raise GatesealError(
            "invalid_field",
            f"{field}.args must hold {RANGE} once, and only a tool "
            "whose hooks run its prepared executable scans history.",
        )
    return history


def _choices(value: object, field: str, owned: Mapping[str, str]) -> dict[str, Choice]:
    choices = parse_choices(value, field)
    if choices and "configuration" not in owned:
        raise GatesealError(
            "invalid_field",
            f"{field} needs a configuration for its choices to render into.",
        )
    return choices


def parse_definition(
    name: str,
    origin: Origin,
    document: Mapping[str, object],
    parse_source: Callable[[object, str], Source],
) -> ToolDefinition:
    """Validate a definition's shared fields; its kind's parse_source reads [source].

    The caller has read kind. The other closed values are read before any
    other field, so a newer compiler's value stops with the step to use it.
    stages must name every Stage, since this compiler renders every tool into
    every stage. A kind whose hooks are local needs an executable, and each
    of its probes must start with that executable, which the link replaces.
    """
    field = f"tools.{name}"
    stages = _stages(document.get("stages"), f"{field}.stages")
    scope = closed(document.get("scope"), Scope, f"{field}.scope")
    owned: dict[str, str] = {}
    if "configuration" in document:
        table = fields(document["configuration"], {"format"}, f"{field}.configuration")
        chosen = closed(
            table["format"], ConfigurationFormat, f"{field}.configuration.format"
        )
        owned["configuration"] = f"{OWNED}/{name}.{chosen}"
    shared = {"kind", "stages", "scope", "removed_prefixes", "source", "hooks"}
    document = fields(
        document,
        shared
        | (
            {"configuration", "executable", "files", "history", "choices"}
            & set(document)
        ),
        field,
    )
    prefixes = _removed_prefixes(
        document["removed_prefixes"], f"{field}.removed_prefixes"
    )
    source = parse_source(document["source"], f"{field}.source")
    files = _files(name, document.get("files", {}), f"{field}.files")
    owned |= {key: path for key, (path, _) in files.items()}
    executable = (
        _executable(document["executable"], f"{field}.executable", owned)
        if "executable" in document
        else None
    )
    hooks = _hooks(name, origin, document["hooks"], owned)
    if source.local and (
        executable is None
        or any(hook.probe[0] != executable.name for hook in hooks.values())
    ):
        raise GatesealError(
            "invalid_field",
            f"A {source.kind} tool needs an executable that starts every probe.",
        )
    history = (
        _history(document["history"], f"{field}.history", owned, source)
        if "history" in document
        else None
    )
    choices = _choices(document.get("choices", {}), f"{field}.choices", owned)
    return ToolDefinition(
        name=name,
        origin=origin,
        stages=stages,
        scope=scope,
        removed_prefixes=prefixes,
        configuration=owned.get("configuration"),
        files={path: content for path, content in files.values()},
        executable=executable,
        hooks=hooks,
        source=source,
        history=history,
        choices=choices,
    )


def pattern(
    value: object, field: str, required: Collection[str], optional: Collection[str] = ()
) -> str:
    """Validate a file or tag name pattern and its placeholders.

    Each required placeholder appears once and each optional one at most once.
    After substitution the name holds only letters, digits, ., _, + and -,
    which keeps a URL's query, fragment and path separators out of it.
    """
    text = string(value, field)
    sample = text
    for placeholder in (*required, *optional):
        count = text.count("{" + placeholder + "}")
        if count > 1 or (count == 0 and placeholder in required):
            sample = ""
        sample = sample.replace("{" + placeholder + "}", "0")
    if not re.fullmatch(r"[A-Za-z0-9._+-]+", sample) or sample in (".", ".."):
        named = ", ".join("{" + p + "}" for p in required)
        raise GatesealError(
            "invalid_field",
            f"{field} must be a name of letters, digits, ., _, + and -"
            + (f" with {named} once." if named else "."),
        )
    return text


def https_url(value: object, field: str) -> str:
    """Validate a repository's https URL, without credentials, query or final slash."""
    text = string(value, field)
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._-]+)+", text):
        raise GatesealError(
            "invalid_field", f"{field} must be an https URL of a repository."
        )
    return text


class Tool(Protocol):
    """One tool as a policy offers it, at one selected reviewed version.

    Each kind implements it with its own release type, so the kind's module
    reads its release fields without a cast. Code outside the kind's module
    sees only this protocol.
    """

    @property
    def definition(self) -> ToolDefinition: ...
    @property
    def use(self) -> ToolUse: ...
    @property
    def pin(self) -> str: ...
    @property
    def versions(self) -> Mapping[str, Release]: ...
    @property
    def version(self) -> str:
        """The selected version: the pin, or a Version Exception's version."""
        ...

    @property
    def release(self) -> Release: ...

    def select(self, version: str) -> Tool:
        """Return this tool at another of its reviewed versions."""
        ...

    def using(self, hooks: tuple[Hook, ...]) -> Tool:
        """Return this tool running only hooks, some of its use's in their order."""
        ...

    def entry(self, exclude: str | None, python: str | None) -> dict[str, object]:
        """Return its one repository in the native pre-commit configuration.

        python is the language_version that names the Hook Python, which a kind
        whose hooks run on Python renders for each hook.
        """
        ...

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the Lock section, key and record of this tool."""
        ...


def owned_outputs(tool: Tool, chosen: Mapping[str, object]) -> dict[str, bytes]:
    """Return the owned outputs a tool's definition declares, by path.

    They are the policy's configuration at the definition's configuration
    path and each fixed file. chosen holds the selection's choices of the
    tool's capability. Each of the definition's choices that chosen holds
    renders as one line, key = "value", before the policy's configuration and
    in definition order, so it stays a top-level key whatever tables the policy
    declares. Without one, the configuration is the policy's bytes. Outputs a
    kind owns across its tools, such as mise.toml, come from the kind.
    """
    outputs = dict(tool.definition.files)
    # pytestmut: off[bool.and_or] -- a use has one when its definition does
    if tool.definition.configuration and tool.use.configuration is not None:
        # pytestmut: on
        keys = "".join(
            f'{choice.key} = "{chosen[name]}"\n'
            for name, choice in tool.definition.choices.items()
            if name in chosen
        )
        outputs[tool.definition.configuration] = keys.encode() + tool.use.configuration
    return outputs


def local_repository(
    tool: str, hooks: Iterable[Hook], exclude: str | None
) -> dict[str, object]:
    """Render hooks that run a prepared executable through the attempt's tools link.

    No single kind owns this rendering: the release-binary kind uses it today
    and a planned project-environment kind will. Each entry is the link
    gateseal-<tool> and the hook's args, joined so pre-commit's shlex split
    returns them exactly; pre-commit appends file names and starts no shell.
    Its hooks need LOCAL_MINIMUM_PRE_COMMIT.
    """
    return {
        "repo": "local",
        "hooks": [
            hook.base_entry(exclude)
            | {
                "language": "unsupported",
                "entry": shlex.join([TOOL_PREFIX + tool, *hook.args]),
            }
            for hook in hooks
        ],
    }


def require_local_hooks(hooks: Iterable[Hook], pre_commit: str) -> None:
    """Require the pre-commit this compiler runs to run each local hook."""
    for hook in hooks:
        if version_key(pre_commit) < version_key(LOCAL_MINIMUM_PRE_COMMIT):
            raise GatesealError(
                "incompatible_runtime",
                f"The {hook.id} hook needs pre-commit {LOCAL_MINIMUM_PRE_COMMIT} or "
                f"newer, but gateseal {__version__} runs pre-commit {pre_commit}.",
                next_step="Use a gateseal release whose pre-commit is "
                f"{LOCAL_MINIMUM_PRE_COMMIT} or newer.",
            )
