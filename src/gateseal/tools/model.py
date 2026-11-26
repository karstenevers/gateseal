"""Tool Definitions, and what every Tool Kind shares.

A Tool Definition is data that parameterizes one Tool Kind. Each kind's module
owns its [source] table, its policy release fields, its rendering and its Lock
record. This module holds what every kind shares, so no
kind renders a hook argument, an owned output or a local hook its own way.

Every placeholder is resolved, and every closed value checked, when a
definition is parsed. Code that holds a ToolDefinition trusts it.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import KW_ONLY, dataclass
from enum import Enum, StrEnum, auto
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, TypeVar

from gateseal import __version__
from gateseal.diagnostics import GatesealError
from gateseal.tools.choices import Choice, parse_choices
from gateseal.tools.configurations import (
    FORMATS,
    Configuration,
    ConfigurationFormat,
    Presets,
)
from gateseal.validation import fields, mapping, sequence, string, version_key

if TYPE_CHECKING:
    from gateseal.tools.node import Node

OWNED = ".gateseal"
"""The directory of every owned output except the native configuration."""

LOCAL_MINIMUM_PRE_COMMIT = "4.4.0"
"""The oldest pre-commit that runs a hook as local_repository renders it.

Its language, unsupported, came with pre-commit 4.4.0.
"""

NAME = re.compile(r"[a-z][a-z0-9-]*")
"""A tool's, hook's or file's name, as owned paths use it."""

REPORT = re.compile(r"[a-z][a-z0-9-]*\.[a-z0-9]+")
"""A report's name: a file that one hook writes and a later hook reads."""

PROGRAM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
"""An executable's bare name, which its kind finds in the tool it installs."""

RANGE = "{range}"
"""The placeholder a history scan's arguments hold once for the gate's range."""


class Stage(StrEnum):
    COMMIT = "commit"
    RUN = "run"
    PUSH = "push"
    CI = "ci"


HOOK_STAGES: Mapping[Stage, str] = {
    Stage.COMMIT: "pre-commit",
    Stage.RUN: "pre-commit",
    Stage.PUSH: "pre-push",
    Stage.CI: "manual",
}
"""The pre-commit stage that runs each Stage's hooks, as --hook-stage names it."""

LAUNCHERS = (HOOK_STAGES[Stage.COMMIT], HOOK_STAGES[Stage.PUSH])
"""The Git hooks that start pre-commit in a clone: its commit and push launchers."""

AT_COMMIT = frozenset({Stage.COMMIT, Stage.RUN, Stage.CI})
"""The stages of a hook that runs at a commit, in gateseal run and in the gate."""

STAGE_SETS: Mapping[frozenset[Stage], tuple[str, ...]] = {
    stages: tuple(
        dict.fromkeys(HOOK_STAGES[stage] for stage in Stage if stage in stages)
    )
    for stages in (AT_COMMIT, frozenset({Stage.PUSH, Stage.CI}), frozenset({Stage.CI}))
}
"""The stages a hook may list, and the stages pre-commit runs it in for them.

pre-commit cannot tell a commit from gateseal run, so commit and run go
together. A hook outside the ci stage would not be part of the gate, so a
hook too slow for a commit runs at a push and in the gate.
"""


class Scope(StrEnum):
    """What a hook checks.

    A FILES hook gets the names of the changed files that match it. A PROJECT
    hook gets no file names and checks the whole project, because a change in
    one file can break another that the commit does not change. Without file
    types it starts at every run. With file types it starts only when a file
    of those types changed, so a change that only deletes such a file does
    not start it: pre-commit matches no hook against a deleted file.
    """

    FILES = "files"
    PROJECT = "project"


class Starts(Enum):
    """What the hooks of a Tool Kind start.

    The hooks of an UPSTREAM_HOOKS kind come from the tool's own repository,
    so its definition has no executable. The hooks of an EXECUTABLE kind
    start the tool's own executable, as local_repository renders them. A
    RECORDED_EXECUTABLE kind does the same and records what the commit check
    compares an installed executable with.
    """

    UPSTREAM_HOOKS = auto()
    EXECUTABLE = auto()
    RECORDED_EXECUTABLE = auto()


class OutputFormat(StrEnum):
    """How a hook reports.

    CONCISE prints each finding as file:line:column: rule message; PLAIN
    reports only that the hook passed, failed or modified files.
    """

    CONCISE = "concise"
    PLAIN = "plain"


class Origin(StrEnum):
    """Who wrote a hook.

    gateseal ships a BUILT_IN definition in its package. A policy's tools/
    folder holds a TEAM one, which carries its kind's guarantees but the
    tool-specific ones, such as turning off a scanner's ignore file, only as
    far as its arguments declare them. A Manifest's [hooks] table holds a
    REPOSITORY hook, which runs a file of the repository and has no definition.
    """

    BUILT_IN = "built-in"
    TEAM = "team-defined"
    REPOSITORY = "repository"


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
    new version's refusal from a reviewed one's, and release what upstream
    lacks, by default the version.
    """

    def __init__(
        self, code: str, message: str, name: str, version: str, release: str = ""
    ) -> None:
        self.release = release or f"{name} {version}"
        super().__init__(
            code,
            message,
            next_step=f"Check that {self.release} is released, or remove it "
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


E = TypeVar("E", bound=StrEnum)


def closed(value: object, members: type[E], field: str) -> E:
    """Return value as a member of a closed set, or fail through unsupported."""
    text = string(value, field)
    try:
        return members(text)
    except ValueError:
        raise unsupported(field, text) from None


@dataclass(frozen=True)
class Hook:
    """One hook that a selection runs, with every placeholder resolved.

    tool is the definition's name, or a Repository Hook's file, and origin
    says who wrote the hook. id and name are unique among the hooks a
    selection runs, gateseal's check hook included: the Lock and run's results
    name a hook by its id, and pre-commit's status line by its name. args
    follow the program that runs the hook, never through a shell. reads and
    writes name the reports that the hook takes from an earlier hook and
    leaves for a later one, each at its report_path. finding_exit_codes
    holds the exit codes with which the tool reports a finding; any other
    failure means the tool stopped before it could check. When it is empty,
    every failure is a finding.
    """

    tool: str
    origin: Origin
    id: str
    name: str
    args: tuple[str, ...]
    types_or: tuple[str, ...]
    output: OutputFormat
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    finding_exit_codes: frozenset[int] = frozenset()

    def base_entry(
        self, exclude: str | None, stages: frozenset[Stage], scope: Scope
    ) -> dict[str, object]:
        """Return the native configuration keys every rendering of the hook shares.

        stages is the definition's, which STAGE_SETS holds, and scope the
        definition's too.
        """
        entry: dict[str, object] = {
            "id": self.id,
            "name": self.name,
            "stages": list(STAGE_SETS[stages]),
        }
        if scope is Scope.PROJECT:
            # Explicit false also overrides a hook repository's always_run.
            entry["always_run"] = not self.types_or
            entry["pass_filenames"] = False
        if self.types_or:
            entry["types_or"] = list(self.types_or)
        if exclude is not None:
            entry["exclude"] = exclude
        return entry


@dataclass(frozen=True)
class Executable:
    """The executable that a tool's hooks start.

    check_at_commit says that the commit check compares the installed program
    with the SHA-256 that the Policy Lock records for it.
    """

    name: str
    check_at_commit: bool


@dataclass(frozen=True)
class Release:
    """One reviewed version of a tool. Each kind adds its Policy Lock facts.

    requires names the oldest version of each other tool that this one works
    with. presets holds the blocks of the presets that the tool's ESLint rule
    file names, as the Policy Lock records them for this version; it is empty
    for every other tool. The format renders them.
    """

    version: str
    requires: Mapping[str, str]
    _: KW_ONLY
    presets: Presets = ()


@dataclass(frozen=True)
class Catalog:
    """A component as policy.toml lists it: its pin, reviewed versions and packages.

    requires holds what each reviewed version requires, by version; pin is
    one of them. packages maps each npm package that every reviewed version
    installs beside a tool on Node to its exact version, by name; it is empty
    for any other tool.
    """

    pin: str
    requires: Mapping[str, Mapping[str, str]]
    packages: Mapping[str, str]


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

    def lockfile(
        self, dependencies: Mapping[str, str], tool: str, version: str
    ) -> dict[str, object]:
        """Return npm's lockfile of dependencies, or fail with MissingUpstream."""
        ...

    def evaluate(
        self,
        lockfile: Mapping[str, object],
        program: Path,
        argument: object,
        tool: str,
        version: str,
    ) -> object:
        """Return the JSON that program prints on the policy's Node, in an install.

        npm installs the packages of lockfile into a new folder, without
        install scripts, and fails as lockfile does. program, an ES module of
        gateseal, then runs once with that folder and argument as JSON, so it
        loads exactly those packages. Its failure is tool_failed. tool and
        version name the install in a failure.
        """
        ...


class Source(Protocol):
    """A definition's [source] table as its kind parsed it, and the kind's rules.

    kind is the kind's value of the definition's kind field. starts says
    what the kind's hooks start. remark is what status adds about a tool of
    the kind after the kind's name, or nothing. on_node says that the kind's
    hooks run on the policy's Node, which policy.toml then pins. scans_history
    says that a tool of the kind may scan the history in the CI gate.
    """

    kind: ClassVar[str]
    starts: ClassVar[Starts]
    remark: ClassVar[str]
    on_node: ClassVar[bool]
    scans_history: ClassVar[bool]

    def offer(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        records: Mapping[str, object],
        node: Node | None,
    ) -> Tool:
        """Return the tool at its pin, each reviewed version with its facts.

        records holds each reviewed version's Policy Lock record, by version.
        A field it finds invalid fails with invalid_field. node is the
        policy's Node, or None when policy.toml pins none; a kind that does
        not run on Node ignores it.
        """
        ...

    def lock(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Return a reviewed version's Policy Lock record, resolved from upstream.

        use is how the policy uses the tool, as offer gets it; a kind whose
        record also holds facts of the tool's configuration, such as an ESLint
        rule file's presets, reads it.
        catalog is the tool as policy.toml lists it. recorded is the record
        the lock holds for the version, or None. Each fact it records must
        equal upstream's, or the version fails with upstream_changed. Only a
        fact it lacks is resolved anew and checked.
        """
        ...


@dataclass(frozen=True, kw_only=True)
class ToolDefinition:
    """One tool as data: the fields every kind shares, and its kind's source.

    name comes from the file name, and origin from where the file lives.
    configuration names the policy's configuration's format and owned output;
    files maps each fixed owned output, .gateseal/<name>-<key>.<extension>, to
    its bytes. Both are derived from the name, so a definition cannot name a
    path the compiler does not own.
    stages is one of STAGE_SETS' sets, which every hook's stages render, and
    scope says whether its hooks get file names. history holds the arguments
    of the tool's executable that scan a revision range, one of which holds
    RANGE, where the CI gate puts the range; only a kind whose hooks are local
    has one.
    choices holds the Policy Choices the definition declares, by name; only a
    definition with a toml configuration declares any, since each renders into
    it.
    """

    name: str
    origin: Origin
    stages: frozenset[Stage]
    scope: Scope
    configuration: Configuration | None
    files: Mapping[str, bytes]
    executable: Executable | None
    hooks: Mapping[str, Hook]
    source: Source
    history: tuple[str, ...] | None
    choices: Mapping[str, Choice]

    @property
    def owned(self) -> tuple[str, ...]:
        """Return every owned output the definition claims."""
        configuration = self.configuration
        return (*(() if configuration is None else (configuration.path,)), *self.files)


def report_path(name: str) -> str:
    """Return the path of the report name, which Git ignores and no Lock records."""
    return f"{OWNED}/reports/{name}"


def _arguments(
    value: object, field: str, owned: Mapping[str, str], kept: str = ""
) -> tuple[str, ...]:
    """Validate literal arguments, resolving each exact {<owned key>} to its path.

    A hook's owned also holds its reports. Any other brace fails, so an
    argument reaches an owned output only through
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
                "exactly {configuration} or {<files key>}, and a report that its "
                "hook reads or writes only as exactly {<report name>}.",
            )
        arguments.append(text)
    return tuple(arguments)


def _program(value: str, field: str) -> str:
    if not PROGRAM.fullmatch(value):
        raise GatesealError(
            "invalid_field", f"{field} must be a bare executable name, not {value!r}."
        )
    return value


def _executable(value: object, field: str) -> Executable:
    table = mapping(value, field)
    table = fields(table, {"name"} | ({"check_at_commit"} & set(table)), field)
    checked = table.get("check_at_commit", False)
    if not isinstance(checked, bool):
        raise GatesealError(
            "invalid_field", f"{field}.check_at_commit must be true or false."
        )
    return Executable(
        _program(string(table["name"], f"{field}.name"), f"{field}.name"),
        checked,
    )


def _source_executable(value: object, tool: str, source: Source) -> Executable | None:
    """Return the executable a definition names, as its kind allows one.

    A kind whose hooks are local needs an executable, and only such a kind
    has one. Only a kind that records the installed program checks it at
    commit.
    """
    field = f"{tool}.executable"
    upstream = source.starts is Starts.UPSTREAM_HOOKS
    if value is None:
        if not upstream:
            raise GatesealError(
                "invalid_field", f"A {source.kind} tool needs an executable."
            )
        return None
    executable = _executable(value, field)
    if upstream:
        raise GatesealError(
            "invalid_field",
            f"{field} needs a tool whose hooks start its own executable.",
        )
    if executable.check_at_commit and source.starts is not Starts.RECORDED_EXECUTABLE:
        raise GatesealError(
            "invalid_field",
            f"{field}.check_at_commit needs a release-binary tool: nothing "
            f"records the program that a {source.kind} tool installs.",
        )
    return executable


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


def _reports(value: object, field: str) -> tuple[str, ...]:
    """Return the report names that field lists, each a REPORT named once."""
    names = tuple(string(item, field) for item in sequence(value, field))
    if len(set(names)) != len(names) or not all(map(REPORT.fullmatch, names)):
        raise GatesealError(
            "invalid_field",
            f"{field} lists report names of lowercase letters, digits and -, with "
            "an extension, such as coverage.json, each once.",
        )
    return names


def _exit_codes(value: object, field: str) -> frozenset[int]:
    """Return the exit codes that field lists: at least one, each 1 to 255 once."""
    items = sequence(value, field)
    codes = frozenset(c for c in items if type(c) is int and 1 <= c <= 255)
    if not items or len(codes) != len(items):
        raise GatesealError(
            "invalid_field",
            f"{field} lists at least one exit code from 1 to 255, each once.",
        )
    return codes


def _hook(
    tool: str, origin: Origin, hook_id: str, value: object, owned: Mapping[str, str]
) -> Hook:
    field = f"tools.{tool}.hooks.{hook_id}"
    table = mapping(value, field)
    output = closed(table.get("output"), OutputFormat, f"{field}.output")
    optional = {"args", "types_or", "reads", "writes", "finding_exit_codes"}
    table = fields(table, {"name", "output"} | (optional & set(table)), field)
    if not NAME.fullmatch(hook_id):
        raise GatesealError("invalid_field", f"{field} needs a lowercase id.")
    reads = _reports(table.get("reads", []), f"{field}.reads")
    writes = _reports(table.get("writes", []), f"{field}.writes")
    if set(reads) & set(writes):
        raise GatesealError(
            "invalid_field", f"{field}.writes names a report that the hook reads."
        )
    paths = {name: report_path(name) for name in (*reads, *writes)}
    return Hook(
        tool,
        origin,
        hook_id,
        string(table["name"], f"{field}.name"),
        _arguments(table.get("args", []), f"{field}.args", {**owned, **paths}),
        tuple(
            string(item, f"{field}.types_or")
            for item in sequence(table.get("types_or", []), f"{field}.types_or")
        ),
        output,
        reads,
        writes,
        (
            _exit_codes(table["finding_exit_codes"], f"{field}.finding_exit_codes")
            if "finding_exit_codes" in table
            else frozenset()
        ),
    )


def stage_set(names: Sequence[str]) -> frozenset[Stage] | None:
    """Return STAGE_SETS' set that names lists, each stage once in any order."""
    return next(
        (stages for stages in STAGE_SETS if sorted(stages) == sorted(names)), None
    )


def _stages(value: object, field: str) -> frozenset[Stage]:
    listed = [string(item, field) for item in sequence(value, field)]
    stages = stage_set(listed)
    if stages is None:
        raise unsupported(field, ", ".join(listed))
    return stages


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
    if not source.scans_history:
        raise GatesealError(
            "invalid_field", f"A {source.kind} tool cannot have {field}."
        )
    if sum(arg.count(RANGE) for arg in history) != 1:
        raise GatesealError("invalid_field", f"{field}.args must hold {RANGE} once.")
    return history


def _configuration(name: str, value: object, field: str) -> Configuration:
    """Read [configuration]: exactly format, whose output names the owned path."""
    table = fields(value, {"format"}, field)
    chosen = closed(table["format"], ConfigurationFormat, f"{field}.format")
    return Configuration(chosen, f"{OWNED}/{name}.{FORMATS[chosen].output}")


def _choices(
    value: object, field: str, configuration: Configuration | None
) -> dict[str, Choice]:
    """Return the choices, which render into a toml configuration."""
    choices = parse_choices(value, field)
    if choices and (
        configuration is None or not FORMATS[configuration.format].takes_choices
    ):
        raise GatesealError(
            "invalid_field",
            f"{field} needs a toml configuration for its choices to render into.",
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
    A kind whose hooks are local needs an executable, and only such a kind
    has one.
    """
    field = f"tools.{name}"
    stages = _stages(document.get("stages"), f"{field}.stages")
    scope = closed(document.get("scope"), Scope, f"{field}.scope")
    configuration = (
        _configuration(name, document["configuration"], f"{field}.configuration")
        if "configuration" in document
        else None
    )
    owned = {} if configuration is None else {"configuration": configuration.path}
    shared = {"kind", "stages", "scope", "source", "hooks"}
    document = fields(
        document,
        shared
        | (
            {"configuration", "executable", "files", "history", "choices"}
            & set(document)
        ),
        field,
    )
    source = parse_source(document["source"], f"{field}.source")
    files = _files(name, document.get("files", {}), f"{field}.files")
    owned |= {key: path for key, (path, _) in files.items()}
    executable = _source_executable(document.get("executable"), field, source)
    hooks = _hooks(name, origin, document["hooks"], owned)
    history = (
        _history(document["history"], f"{field}.history", owned, source)
        if "history" in document
        else None
    )
    choices = _choices(document.get("choices", {}), f"{field}.choices", configuration)
    return ToolDefinition(
        name=name,
        origin=origin,
        stages=stages,
        scope=scope,
        configuration=configuration,
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
    @property
    def node(self) -> Node | None:
        """The policy's Node that the tool's hooks run on, or None."""
        ...

    def select(self, version: str) -> Tool:
        """Return this tool at another of its reviewed versions."""
        ...

    def using(self, hooks: tuple[Hook, ...]) -> Tool:
        """Return this tool running only hooks, some of its use's in their order."""
        ...

    def program(self, variables: Collection[str] = ()) -> tuple[str, ...] | None:
        """Return the command that starts the tool's executable, or None.

        Only a kind whose hooks are local has one. variables names the caller's
        environment variables that the executable also gets. The gate starts a
        history scan with it.
        """
        ...

    def entry(self, exclude: str | None) -> dict[str, object]:
        """Return its one repository in the native pre-commit configuration."""
        ...

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the Lock section, key and record of this tool."""
        ...


def exclusion(paths: tuple[str, ...]) -> str:
    """Return pre-commit's exclude pattern for paths and everything under them.

    A path choice holds only letters, digits, ., _, - and /, of which only .
    has a meaning in a pattern.
    """
    escaped = (path.replace(".", "\\.") for path in paths)
    return "^(?:" + "|".join(path + "(?:/|$)" for path in escaped) + ")"


def local_repository(
    program: Sequence[str],
    hooks: Iterable[Hook],
    exclude: str | None,
    stages: frozenset[Stage],
    scope: Scope,
) -> dict[str, object]:
    """Render hooks that start a tool's own executable with program.

    Each entry is program and the hook's args, joined so pre-commit's shlex
    split returns them exactly; pre-commit appends the file names of a FILES
    scope and starts no shell. Its hooks need LOCAL_MINIMUM_PRE_COMMIT.
    pre-commit starts one process of a hook at a time, because two first
    starts of one program at once can fail while the program is installed.
    """
    return {
        "repo": "local",
        "hooks": [
            hook.base_entry(exclude, stages, scope)
            | {
                "language": "unsupported",
                "entry": shlex.join([*program, *hook.args]),
                "require_serial": True,
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
