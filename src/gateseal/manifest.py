"""The Manifest, and what it selects from one policy release."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from gateseal import __version__
from gateseal.bootstrap import MANIFEST
from gateseal.compiler_release import load_runtime
from gateseal.diagnostics import GatesealError, Note
from gateseal.hook_python import HookPython
from gateseal.managed_files import relative_name
from gateseal.policy import (
    ChoiceValue,
    Policy,
    choice_parser,
    require_compatible,
)
from gateseal.policy_tools import CAPABILITY, HYGIENE
from gateseal.tools.model import Hook, Origin, Tool, require_local_hooks
from gateseal.validation import (
    fields,
    mapping,
    parse_toml,
    schema,
    sequence,
    string,
)

HOST = r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::[0-9]{1,5})?"
SEGMENT = r"/(?!\.\.?(?:/|$))[A-Za-z0-9._~%+-]+"
REPOSITORY = re.compile(
    rf"(?:https://|ssh://(?:[A-Za-z0-9_][A-Za-z0-9._-]*@)?){HOST}(?:{SEGMENT})+"
)
TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*(?:/[A-Za-z0-9][A-Za-z0-9._+-]*)*")
COMMIT = re.compile(r"[0-9a-f]{40}")
SCP = re.compile(
    r"([A-Za-z0-9_][A-Za-z0-9._-]*@)?([A-Za-z0-9][A-Za-z0-9.-]*):(?!//)(.+)"
)
REASON_LIMIT = 500
CI_PROVIDERS = frozenset({"github"})
"""The CI providers whose workflow this compiler renders."""
BRANCH = re.compile(r"[A-Za-z0-9._/-]+")


@dataclass(frozen=True)
class BundledSource:
    release: str


@dataclass(frozen=True)
class GitSource:
    """A policy release in a Git repository, named without credentials.

    commit selects the release. tag is the release label that adoption checks
    names commit in the repository; later commands only display it.
    """

    repository: str
    path: str
    commit: str
    tag: str


Source = BundledSource | GitSource


def _is_repository_url(location: str) -> bool:
    return bool(REPOSITORY.fullmatch(location)) and not location.endswith((".", "/"))


def _example(location: str) -> str:
    """Return the ssh:// URL of a scp-like address, or a generic example.

    The ssh:// form is offered only when it is itself a valid repository URL,
    since the address could hold a credential.
    """
    scp = SCP.fullmatch(location)
    if scp and "/" not in scp[2]:
        candidate = f"ssh://{scp[1] or ''}{scp[2]}/{scp[3]}"
        if _is_repository_url(candidate):
            return candidate
    return "https://github.com/team/gateseal-policy.git"


def git_source(
    repository: object, path: object, commit: object, tag: object
) -> GitSource:
    """Validate a Git policy source from the Manifest or configure's flags.

    An invalid repository URL is never repeated, since it could hold a
    credential.
    """
    location = string(repository, "policy.repository")
    if not _is_repository_url(location):
        raise GatesealError(
            "invalid_policy_source",
            "policy.repository must be an https:// or ssh:// Git URL with a host "
            "and a path and no password, token, query, or fragment.",
            next_step="Name the repository without credentials, such as "
            f"{_example(location)}, and let your Git credential helper or SSH "
            "agent authenticate.",
        )
    selected = string(path, "policy.path")
    if selected != ".":
        relative_name(selected)
    revision = string(commit, "policy.commit")
    if not COMMIT.fullmatch(revision):
        raise GatesealError(
            "invalid_policy_source",
            "policy.commit must be a full lowercase Git commit ID.",
        )
    label = string(tag, "policy.tag")
    if not TAG.fullmatch(label) or ".." in label or label.endswith((".", ".lock")):
        raise GatesealError(
            "invalid_policy_source", f"policy.tag {label!r} is not a tag name."
        )
    return GitSource(location, selected, revision, label)


@dataclass(frozen=True)
class ExceptionRequest:
    component: str
    version: str
    reason: str
    review_by: date


@dataclass(frozen=True)
class CI:
    """The CI provider whose workflow configure writes, and the branch it guards.

    default_branch is the branch whose pushes the workflow checks.
    """

    provider: str
    default_branch: str


def _ci(value: object) -> CI:
    table = fields(value, {"provider", "default_branch"}, "manifest.ci")
    provider = string(table["provider"], "ci.provider")
    if provider not in CI_PROVIDERS:
        raise GatesealError(
            "unsupported_ci_provider",
            f"ci.provider is {provider!r}, which gateseal {__version__} does not "
            "implement.",
            next_step="Use a gateseal compiler that implements this CI provider.",
        )
    branch = string(table["default_branch"], "ci.default_branch")
    if (
        not BRANCH.fullmatch(branch)
        or ".." in branch
        or "//" in branch
        or branch.startswith(("/", "."))
        or branch.endswith(("/", ".", ".lock"))
    ):
        raise GatesealError(
            "invalid_field",
            f"ci.default_branch {branch!r} is not a branch name of letters, digits, "
            "., _, - and /.",
        )
    return CI(provider, branch)


@dataclass(frozen=True)
class Manifest:
    capabilities: tuple[str, ...]
    source: Source
    choices: Mapping[str, Mapping[str, object]]
    exceptions: tuple[ExceptionRequest, ...]
    ci: CI | None


def capabilities(names: list[object]) -> tuple[str, ...]:
    """Return a capability selection in canonical order, sorted by name.

    Which capabilities exist is the policy's to say, so resolve checks them.
    """
    selected = [string(name, "capabilities") for name in names]
    if (
        len(set(selected)) != len(selected)
        or not all(map(CAPABILITY.fullmatch, selected))
        or HYGIENE not in selected
    ):
        raise GatesealError(
            "unsupported_capability",
            f"Select {HYGIENE}, optionally with other capabilities the policy "
            "offers, such as python and secrets, each at most once.",
        )
    return tuple(sorted(selected))


def _source(value: object) -> Source:
    table = mapping(value, "manifest.policy")
    kind = table.get("source")
    if kind == "bundled":
        selection = fields(table, {"source", "release"}, "manifest.policy")
        return BundledSource(string(selection["release"], "policy.release"))
    if kind == "git":
        names = {"source", "repository", "path", "commit", "tag"}
        selection = fields(table, names, "manifest.policy")
        return git_source(
            selection["repository"],
            selection["path"],
            selection["commit"],
            selection["tag"],
        )
    raise GatesealError(
        "unsupported_source", 'policy.source must be "bundled" or "git".'
    )


def _exception(component: str, value: object) -> ExceptionRequest:
    field = f"exceptions.{component}"
    entry = fields(value, {"version", "reason", "review_by"}, field)
    reason = entry["reason"]
    if (
        not isinstance(reason, str)
        or not reason.strip()
        or len(reason) > REASON_LIMIT
        or not reason.isprintable()
    ):
        raise GatesealError(
            "invalid_exception",
            f"{field}.reason must say why on one line, in at most {REASON_LIMIT} "
            "characters.",
        )
    review_by = entry["review_by"]
    if type(review_by) is not date:
        raise GatesealError(
            "invalid_exception",
            f"{field}.review_by must be a TOML date such as 2026-12-31, "
            "without quotes or a time.",
        )
    return ExceptionRequest(
        component, string(entry["version"], f"{field}.version"), reason, review_by
    )


def parse_manifest(data: bytes) -> Manifest:
    document = parse_toml(data)
    doc = fields(
        document,
        {"schema", "capabilities", "policy", "choices", "exceptions"}
        | ({"ci"} & set(document)),
        "manifest",
    )
    schema(doc["schema"])
    choices = {
        name: mapping(table, f"choices.{name}")
        for name, table in mapping(doc["choices"], "choices").items()
    }
    return Manifest(
        capabilities(sequence(doc["capabilities"], "capabilities")),
        _source(doc["policy"]),
        choices,
        tuple(
            _exception(name, value)
            for name, value in mapping(doc["exceptions"], "exceptions").items()
        ),
        _ci(doc["ci"]) if "ci" in doc else None,
    )


def policy_lines(source: Source) -> list[str]:
    """Return the [policy] table that selects source, one line per item."""
    values: dict[str, str] = (
        {"source": "bundled", "release": source.release}
        if isinstance(source, BundledSource)
        else {
            "source": "git",
            "repository": source.repository,
            "path": source.path,
            "commit": source.commit,
            "tag": source.tag,
        }
    )
    return [
        "[policy]",
        *(
            f"{key} = {json.dumps(value, ensure_ascii=False)}"
            for key, value in values.items()
        ),
    ]


def render(selected: tuple[str, ...], source: Source, ci: CI | None = None) -> bytes:
    """Return a new Manifest that selects source with no choices or exceptions."""
    lines = [
        "schema = 1",
        f"capabilities = {json.dumps(list(selected))}",
        "",
        *policy_lines(source),
    ]
    if ci is not None:
        lines += [
            "",
            "[ci]",
            f"provider = {json.dumps(ci.provider)}",
            f"default_branch = {json.dumps(ci.default_branch)}",
        ]
    return ("\n".join([*lines, "", "[choices]", "", "[exceptions]"]) + "\n").encode()


@dataclass(frozen=True)
class VersionException:
    """A validated exception: the reviewed version that replaces a pin, and why."""

    component: str
    version: str
    pin: str
    reason: str
    review_by: date

    def document(self) -> dict[str, object]:
        return {
            "version": self.version,
            "pin": self.pin,
            "reason": self.reason,
            "review_by": self.review_by.isoformat(),
        }


@dataclass(frozen=True)
class Selection:
    """What a Manifest selects from one policy: the inputs a Lock records.

    tools holds every tool the capabilities use, in capability order, each at
    its selected version with exceptions applied and running only the chosen
    alternatives. python is the policy's Hook Python when a selected tool
    runs on Python. ci is the Manifest's.
    """

    choices: Mapping[str, Mapping[str, ChoiceValue]]
    exceptions: Mapping[str, VersionException]
    tools: tuple[Tool, ...]
    python: HookPython | None
    ci: CI | None

    @property
    def hooks(self) -> tuple[Hook, ...]:
        """Return every hook the selection runs, in configuration order."""
        return tuple(hook for tool in self.tools for hook in tool.use.hooks)


def resolve(manifest: Manifest, policy: Policy) -> Selection:
    """Validate the Manifest's choices and exceptions against policy.

    The selection's tools are the policy's tools of the selected capabilities,
    in policy order. Every exception must name a reviewed version of one,
    the releases with every exception applied must meet each other's
    requirements, and the pre-commit this compiler runs must run every hook.
    Each tool's release must accept the value of every choice its definition
    declares, the policy's default included.
    """
    _require_offered(manifest, policy)
    choices = _choice_values(manifest, policy)
    used = _chosen(policy, manifest.capabilities, choices)
    exceptions = {
        request.component: _version_exception(request, policy, used)
        for request in manifest.exceptions
    }
    tools = tuple(
        tool.select(exceptions[name].version) if name in exceptions else tool
        for name, tool in used.items()
    )
    require_compatible(
        {tool.definition.name: tool.release for tool in tools},
        "incompatible_exception",
        "exceptions",
    )
    require_local_hooks(
        (
            hook
            for tool in tools
            if tool.definition.source.local
            for hook in tool.use.hooks
        ),
        load_runtime().pre_commit,
    )
    _require_accepted(tools, choices)
    python = policy.hook_python
    if python is not None and (
        python.on_runtime or not any(tool.definition.source.python for tool in tools)
    ):
        python = None
    return Selection(
        choices, dict(sorted(exceptions.items())), tools, python, manifest.ci
    )


def _require_offered(manifest: Manifest, policy: Policy) -> None:
    """Require policy to offer each capability and choice the Manifest names.

    Each choice must also apply to a capability the Manifest selects.
    """
    selected = manifest.capabilities
    missing = [name for name in selected if name not in policy.capabilities]
    if missing:
        raise GatesealError(
            "unsupported_capability",
            f"The selected policy offers no {', '.join(missing)} capability.",
        )
    for capability, values in manifest.choices.items():
        for name in values:
            if (capability, name) not in policy.choices:
                raise GatesealError(
                    "unsupported_choice",
                    f"The policy exposes no choice {capability}.{name}.",
                )
        if capability not in selected:
            raise GatesealError(
                "unused_choice",
                f"choices.{capability} applies to {capability}, which the Manifest "
                "does not select.",
            )


def _choice_values(
    manifest: Manifest, policy: Policy
) -> dict[str, dict[str, ChoiceValue]]:
    """Return the value of each choice that the selected capabilities expose.

    A value the Manifest gives replaces the policy's default.
    """
    choices: dict[str, dict[str, ChoiceValue]] = {}
    for (capability, name), default in policy.choices.items():
        if capability in manifest.capabilities:
            given = manifest.choices.get(capability, {})
            value = (
                choice_parser(policy.declared, capability, name)(
                    given[name], f"choices.{capability}.{name}"
                )
                if name in given
                else default
            )
            choices.setdefault(capability, {})[name] = value
    return choices


def _version_exception(
    request: ExceptionRequest, policy: Policy, used: Mapping[str, Tool]
) -> VersionException:
    """Validate request against the tools the selection uses."""
    if request.component == "pre-commit":
        raise GatesealError(
            "runtime_exception",
            "exceptions.pre-commit selects a pre-commit version, but each "
            f"gateseal release carries its own: gateseal {__version__} runs "
            f"pre-commit {load_runtime().pre_commit}.",
            next_step=f"Remove [exceptions.pre-commit] from {MANIFEST}, then run "
            "gateseal configure. A newer pre-commit comes with a newer gateseal "
            "release.",
        )
    tool = used.get(request.component)
    if tool is None:
        offered = request.component in policy.tools
        raise GatesealError(
            "unused_exception" if offered else "unsupported_component",
            f"exceptions.{request.component} names a component that "
            + (
                "the selected capabilities and alternatives do not use."
                if offered
                else "the policy does not offer."
            ),
        )
    if request.version not in tool.versions:
        raise GatesealError(
            "unknown_version",
            f"The policy has not reviewed {request.component} "
            f"{request.version}. Reviewed versions: "
            f"{', '.join(sorted(tool.versions))}.",
            next_step=f"Select a reviewed version in exceptions."
            f"{request.component}, or ask the policy's maintainers to review "
            "the one you need.",
        )
    return VersionException(
        request.component,
        request.version,
        tool.pin,
        request.reason,
        request.review_by,
    )


def _require_accepted(
    tools: tuple[Tool, ...], choices: Mapping[str, Mapping[str, ChoiceValue]]
) -> None:
    """Require each tool's release to accept every choice its definition declares."""
    for tool in tools:
        capability = tool.use.capability
        for name, choice in tool.definition.choices.items():
            value = choices.get(capability, {}).get(name)
            if isinstance(value, str):
                choice.require(
                    value,
                    tool.definition.name,
                    tool.version,
                    f"choices.{capability}.{name}",
                )


def _chosen(
    policy: Policy,
    selected: tuple[str, ...],
    choices: Mapping[str, Mapping[str, ChoiceValue]],
) -> dict[str, Tool]:
    """Return the tools of the selected capabilities, without unchosen alternatives.

    A tool loses the hooks of every alternative a choice did not pick, and a
    tool left without hooks is not used.
    """
    dropped = {
        hook
        for (capability, name), alternatives in policy.alternatives.items()
        if capability in selected
        for hook in alternatives.hooks
        if hook != choices[capability][name]
    }
    used: dict[str, Tool] = {}
    for name, tool in policy.tools.items():
        hooks = tuple(h for h in tool.use.hooks if h.id not in dropped)
        if tool.use.capability in selected and hooks:
            used[name] = tool.using(hooks) if hooks != tool.use.hooks else tool
    return used


def tool_note(tool: Tool) -> Note:
    """Label a selected tool built-in or team-defined, with its version and kind.

    A team definition carries its kind's guarantees, and the tool-specific
    ones, such as turning off a scanner's ignore file, only as its arguments
    declare them.
    """
    definition = tool.definition
    caveat = (
        "; it carries the tool-specific guarantees only as far as its "
        "definition's arguments declare them"
        if definition.origin is Origin.TEAM
        else ""
    )
    return Note(
        "info",
        "tool",
        f"Tool {definition.name} {tool.version} for {tool.use.capability}: "
        f"{definition.origin}, {definition.source.kind}{caveat}.",
    )


def notes(selection: Selection, today: date) -> tuple[Note, ...]:
    """Describe the selection's choices and exceptions for status and doctor."""
    found: list[Note] = []
    for capability, values in selection.choices.items():
        for name, value in values.items():
            listed = value if isinstance(value, str) else ", ".join(value) or "nothing"
            found.append(
                Note(
                    "info",
                    "policy_choice",
                    f"Choice {capability}.{name}: {listed}.",
                )
            )
    for exception in selection.exceptions.values():
        subject = (
            f"Version exception {exception.component} {exception.version} "
            f"(policy pin {exception.pin}), review by "
            f"{exception.review_by.isoformat()}: {exception.reason}"
        )
        if exception.version == exception.pin:
            found.append(
                Note(
                    "warning",
                    "redundant_exception",
                    f"{subject}. The policy now pins this version; remove the "
                    "exception.",
                )
            )
        elif exception.review_by < today:
            found.append(
                Note(
                    "warning",
                    "overdue_exception",
                    f"{subject}. Its review is overdue; the exception stays in "
                    "effect until you change it.",
                )
            )
        else:
            found.append(Note("info", "version_exception", subject + "."))
    return tuple(found)
