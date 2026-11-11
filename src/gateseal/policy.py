import hashlib
import re
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import cast

from gateseal import __version__, hook_python
from gateseal.diagnostics import GatesealError
from gateseal.hook_python import HookPython
from gateseal.managed_files import read
from gateseal.policy_tools import Alternatives, Toolset, read_toolset
from gateseal.tools import built_in_tools
from gateseal.tools.choices import Choice
from gateseal.tools.model import (
    Catalog,
    Release,
    Tool,
    ToolDefinition,
    ToolUse,
    lock_failure,
)
from gateseal.validation import (
    canonical,
    decode_json,
    fields,
    mapping,
    parse_toml,
    sequence,
    string,
    version,
    version_key,
)

SCHEMAS = frozenset({4})
"""The policy schemas this compiler reads."""

INHERITANCE = frozenset({"base", "extend", "extends", "include", "inherit"})


@dataclass(frozen=True)
class Asset:
    path: str
    content: bytes


POLICY = "policy.toml"
"""The file a policy's authors edit, which names the rest of the policy."""

LOCK = "policy.lock"
"""The Policy Lock: every fact derived from upstream, which nobody edits."""

LOCK_COMMAND = "gateseal policy lock"
"""The command that writes the Policy Lock, which its generated_by names."""

PATH_CHOICE = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
"""A repository-relative path a choice may name: portable characters only, so
its rendering never depends on a regular expression library's escaping."""

ASSET = re.compile(r"[a-z0-9][a-z0-9._-]*(?:/[a-z0-9][a-z0-9._-]*)*")
"""A policy asset name: lowercase, so no two names collide on a case-insensitive
file system."""


def paths_choice(value: object, field: str) -> tuple[str, ...]:
    """Validate a list of unique repository-relative paths without a final slash."""
    names = tuple(string(item, field) for item in sequence(value, field))
    for name in names:
        if not PATH_CHOICE.fullmatch(name) or {".", ".."} & set(name.split("/")):
            raise GatesealError(
                "invalid_choice",
                f"{field} takes repository-relative paths of letters, digits, "
                f"., _ and -, such as vendor or docs/generated, without a final "
                f"slash, not {name!r}.",
            )
    if len(set(names)) != len(names):
        raise GatesealError("invalid_choice", f"{field} lists a path twice.")
    return names


ChoiceValue = str | tuple[str, ...]
"""A choice's value. A definition's choice holds one string, a path choice paths."""

ChoiceParser = Callable[[object, str], ChoiceValue]
"""Return a choice's value for its field, or fail with invalid_choice."""

CHOICES: dict[tuple[str, str], ChoiceParser] = {
    ("hygiene", "exclude"): paths_choice,
}
"""The choices the compiler itself renders, by capability and name.

The definitions of a capability's tools and its alternatives declare the
others.
"""

Declared = Choice | Alternatives
"""A choice that a tool's definition or a capability's alternatives declare."""


def declared_choices(
    tools: Iterable[tuple[str, ToolDefinition]],
    alternatives: Mapping[tuple[str, str], Alternatives],
) -> dict[tuple[str, str], Declared]:
    """Return each choice the policy declares, by capability and name.

    tools pairs each tool's capability with its definition. A capability's
    choices.<capability>.<name> must mean one choice, so no two of its tools
    and alternatives may declare one name, and none may declare a choice of
    CHOICES.
    """
    declared: dict[tuple[str, str], Declared] = {}
    for (capability, name), choice in alternatives.items():
        if (capability, name) in CHOICES:
            raise GatesealError(
                "invalid_field",
                f"capabilities.{capability}.alternatives.{name} declares the "
                f"choice {name}, which gateseal itself declares.",
            )
        declared[capability, name] = choice
    for capability, definition in tools:
        for name, choice in definition.choices.items():
            if (capability, name) in CHOICES or (capability, name) in declared:
                raise GatesealError(
                    "invalid_field",
                    f"tools.{definition.name} declares the choice {name}, which "
                    f"another tool of {capability}, its alternatives or gateseal "
                    "itself declares.",
                )
            declared[capability, name] = choice
    return declared


def choice_parser(
    declared: Mapping[tuple[str, str], Declared], capability: str, name: str
) -> ChoiceParser:
    """Return the validation of the choice name of capability.

    CHOICES comes first, then the choices of declared_choices. A choice
    neither defines fails with unsupported_choice.
    """
    if (capability, name) in CHOICES:
        return CHOICES[capability, name]
    if (capability, name) not in declared:
        raise _undefined_choice(capability, name)
    return declared[capability, name].parse


def _undefined_choice(capability: str, name: str) -> GatesealError:
    return GatesealError(
        "unsupported_choice",
        f"gateseal defines no choice {capability}.{name} for this policy's "
        "capabilities.",
    )


@dataclass(frozen=True)
class Policy:
    """One validated policy release.

    capabilities lists what the policy offers, in the order a selection
    compiles them. tools holds every tool of every capability by name, in
    capability then tool order, at the policy's pin: its reviewed versions,
    the hooks the policy runs and the configuration it copies into the tool's
    owned output. hook_python is the pinned Hook Python, or None to run every
    hook on the runtime's CPython.
    """

    release: str
    minimum_compiler: str
    capabilities: tuple[str, ...]
    tools: Mapping[str, Tool]
    alternatives: Mapping[tuple[str, str], Alternatives]
    choices: Mapping[tuple[str, str], ChoiceValue]
    hook_python: HookPython | None
    assets: tuple[Asset, ...]
    content_digest: str

    @property
    def declared(self) -> dict[tuple[str, str], Declared]:
        """Return the choices that tools and alternatives declare, by key."""
        return declared_choices(
            ((tool.use.capability, tool.definition) for tool in self.tools.values()),
            self.alternatives,
        )


@dataclass(frozen=True)
class Review:
    """What a policy's authors edit: policy.toml and the rule files it names.

    definitions holds every tool the capabilities name, uses how the policy
    uses it, and catalogs its pin and reviewed versions, each by tool name in
    capability then tool order. python catalogs the Hook Python, if the policy
    pins one. The Policy Lock adds each reviewed version's facts.
    """

    release: str
    minimum_compiler: str
    capabilities: tuple[str, ...]
    definitions: Mapping[str, ToolDefinition]
    uses: Mapping[str, ToolUse]
    alternatives: Mapping[tuple[str, str], Alternatives]
    catalogs: Mapping[str, Catalog]
    python: Catalog | None
    choices: Mapping[tuple[str, str], ChoiceValue]
    assets: tuple[Asset, ...]

    @property
    def components(self) -> dict[str, Catalog]:
        """Return every component's catalog by name, the Hook Python's included."""
        python = {} if self.python is None else {hook_python.COMPONENT: self.python}
        return {**self.catalogs, **python}


CONTENT = b"gateseal-policy-content-v1\0"


def content_stream(assets: tuple[Asset, ...]) -> bytes:
    """Return the bytes a policy's content digest covers: every asset by path."""
    records = [CONTENT]
    for asset in sorted(assets, key=lambda a: a.path.encode("utf-8")):
        path = asset.path.encode("utf-8")
        records += [len(path).to_bytes(8, "big"), path]
        records += [len(asset.content).to_bytes(8, "big"), asset.content]
    return b"".join(records)


def content_digest(assets: tuple[Asset, ...]) -> str:
    return "sha256:" + hashlib.sha256(content_stream(assets)).hexdigest()


def parse_content_stream(data: bytes) -> dict[str, bytes]:
    """Return the assets of a content stream by path, or raise ValueError."""
    if not data.startswith(CONTENT):
        raise ValueError("not a policy content stream")
    assets: dict[str, bytes] = {}
    offset = len(CONTENT)

    def take() -> bytes:
        nonlocal offset
        size = int.from_bytes(data[offset : offset + 8], "big")
        start, offset = offset + 8, offset + 8 + size
        if offset > len(data):
            raise ValueError("truncated policy content stream")
        return data[start:offset]

    while offset < len(data):
        path = take().decode("utf-8")
        assets[path] = take()
    return assets


def _compatibility(document: dict[str, object], compiler_version: str) -> str:
    """Check schema and minimum_compiler before any other field is read.

    A later schema or a newer compiler's optional field must stop here with a
    step to change compilers, not as an unknown field.
    """
    found = document.get("schema")
    if type(found) is not int:
        raise GatesealError("invalid_field", "Policy schema must be an integer.")
    supported = ", ".join(str(item) for item in sorted(SCHEMAS))
    if found not in SCHEMAS:
        raise GatesealError(
            "unsupported_schema",
            f"The policy uses schema {found}; gateseal {compiler_version} reads "
            f"policy schema {supported}.",
            next_step="Use a gateseal compiler that reads this policy's schema, "
            "or a policy release for this compiler.",
        )
    minimum = version(document.get("minimum_compiler"), "minimum_compiler")
    if version_key(compiler_version) < version_key(minimum):
        raise GatesealError(
            "incompatible_compiler",
            f"The policy requires gateseal {minimum} or newer; this is "
            f"{compiler_version}.",
            next_step=f"Use gateseal {minimum} or newer.",
        )
    inherited = sorted(INHERITANCE & set(document))
    if inherited:
        raise GatesealError(
            "policy_inheritance",
            f"The policy names {', '.join(inherited)}, but a policy is one "
            "flat release.",
            next_step="Derive the policy from its base when you author it, and "
            "publish the flat result.",
        )
    return minimum


def review_policy(
    read_asset: Callable[[str], bytes], *, compiler: str = __version__
) -> Review:
    """Validate the edited part of one flat policy, whose files read_asset returns.

    read_asset raises FileNotFoundError for a name the source lacks.
    """
    assets: list[Asset] = []

    def asset(value: str, field: str, extension: str) -> bytes | None:
        if not ASSET.fullmatch(value) or {".", ".."} & set(value.split("/")):
            raise GatesealError(
                "unsafe_path",
                f"{field} names {value!r}; policy files have contained relative "
                "names of lowercase letters, digits, ., _ and -.",
            )
        if not value.endswith(extension):
            raise GatesealError(
                "invalid_field",
                f"{field} names {value}, but policy data for it is a {extension} file.",
            )
        if value in {item.path for item in assets}:
            raise GatesealError(
                "duplicate_identifier", "Policy assets must have distinct names."
            )
        try:
            content = read_asset(value)
        except FileNotFoundError:
            return None
        assets.append(Asset(value, content))
        return content

    source = asset(POLICY, "policy", ".toml")
    if source is None:
        raise GatesealError("missing_policy_asset", f"The policy has no file {POLICY}.")
    document = mapping(parse_toml(source), "policy")
    minimum = _compatibility(document, version(compiler, "compiler_version"))
    required = {"schema", "release", "minimum_compiler", "capabilities", "tools"}
    document = fields(document, required | {"choices", "components"}, "policy")
    release = string(document["release"], "release")
    if len(release) > 128 or not release.isprintable():
        raise GatesealError(
            "invalid_field", "Policy release must be a short printable label."
        )
    toolset = read_toolset(
        document["capabilities"], document["tools"], read_asset, asset
    )
    components = dict(mapping(document["components"], "components"))
    python = components.pop(hook_python.COMPONENT, None)
    catalogs = _catalogs(components, toolset.definitions)
    exposed = _exposed(document["choices"], toolset)
    for name, use in toolset.uses.items():
        for choice_name, choice in toolset.definitions[name].choices.items():
            default = exposed.get((use.capability, choice_name))
            if isinstance(default, str):
                field = f"choices.{use.capability}.{choice_name}.default"
                choice.require(default, name, catalogs[name].pin, field)
    return Review(
        release,
        minimum,
        toolset.capabilities,
        toolset.definitions,
        toolset.uses,
        toolset.alternatives,
        catalogs,
        None if python is None else _catalog(hook_python.COMPONENT, python, ()),
        exposed,
        tuple(assets),
    )


def load_policy(
    read_asset: Callable[[str], bytes], *, compiler: str = __version__
) -> Policy:
    """Validate one flat policy whose files read_asset returns by relative name.

    Its Policy Lock must hold exactly the facts of the versions it reviews,
    as gateseal policy lock writes them.
    """
    review = review_policy(read_asset, compiler=compiler)
    try:
        data = read_asset(LOCK)
    except FileNotFoundError:
        raise lock_failure(
            "policy_lock_missing", f"The policy has no {LOCK}."
        ) from None
    records = _records(data, review.components)
    tools: dict[str, Tool] = {}
    python = None
    try:
        for name, use in review.uses.items():
            definition = review.definitions[name]
            tools[name] = definition.source.offer(
                definition, use, review.catalogs[name], records[name]
            )
        if review.python is not None:
            python = hook_python.offer(review.python, records[hook_python.COMPONENT])
    except GatesealError as exc:
        if exc.code != "invalid_field":
            raise
        raise lock_failure("policy_lock_edited", f"{LOCK}: {exc}") from exc
    assets = (*review.assets, Asset(LOCK, data))
    return Policy(
        review.release,
        review.minimum_compiler,
        review.capabilities,
        tools,
        review.alternatives,
        review.choices,
        python,
        assets,
        content_digest(assets),
    )


def _records(
    data: bytes, catalogs: Mapping[str, Catalog]
) -> dict[str, dict[str, object]]:
    """Return each reviewed version's Policy Lock record, by tool and version.

    The lock must be the canonical JSON that gateseal policy lock writes, with
    a record for exactly each version the catalogs review.
    """
    try:
        document = fields(decode_json(data), {"components", "generated_by"}, LOCK)
        records = {
            name: mapping(
                fields(value, {"versions"}, f"components.{name}")["versions"],
                f"components.{name}.versions",
            )
            for name, value in mapping(document["components"], "components").items()
        }
    except GatesealError as exc:
        raise lock_failure("policy_lock_edited", f"{LOCK}: {exc}") from exc
    if document["generated_by"] != LOCK_COMMAND or canonical(document) != data:
        raise lock_failure(
            "policy_lock_edited",
            f"{LOCK} is not the file {LOCK_COMMAND} writes; it was edited.",
        )
    reviewed = {name: catalog.requires for name, catalog in catalogs.items()}
    missing = [
        f"{name} {key}"
        for name, versions in sorted(reviewed.items())
        for key in sorted(versions, key=version_key)
        if key not in records.get(name, {})
    ]
    if missing:
        raise lock_failure(
            "policy_lock_stale",
            f"{LOCK} has no facts for {', '.join(missing)}, which policy.toml reviews.",
        )
    unreviewed = [
        f"{name} {key}"
        for name, versions in sorted(records.items())
        for key in sorted(versions)
        if key not in reviewed.get(name, {})
    ]
    if unreviewed:
        raise lock_failure(
            "policy_lock_unreviewed",
            f"{LOCK} lists {', '.join(unreviewed)}, which policy.toml does not review.",
        )
    return records


def _catalogs(
    components: Mapping[str, object], definitions: Mapping[str, ToolDefinition]
) -> dict[str, Catalog]:
    """Validate the tools' components, which must catalog exactly the policy's tools.

    A version may require a built-in tool or one of the policy's. The pins
    must meet each other's requirements.
    """
    if set(components) != set(definitions):
        raise GatesealError(
            "unsupported_component",
            f"This policy's capabilities need exactly the components "
            f"{', '.join(sorted(definitions))}, and components."
            f"{hook_python.COMPONENT} may pin a Hook Python.",
        )
    known = set(built_in_tools()) | set(definitions)
    catalogs = {
        name: _catalog(name, components[name], known) for name in sorted(components)
    }
    require_compatible(
        {
            name: Release(catalog.pin, catalog.requires[catalog.pin])
            for name, catalog in catalogs.items()
        },
        "incompatible_policy",
        "pins",
    )
    return catalogs


def _catalog(name: str, table: object, known: Collection[str]) -> Catalog:
    """Validate a policy's components.<name>: its pin and reviewed versions.

    Each version key is a stable release, and each version table holds only an
    optional requires that names only known tools and never this one.
    """
    table = fields(table, {"pin", "versions"}, f"components.{name}")
    reviewed: dict[str, Mapping[str, str]] = {}
    for key, entry in mapping(table["versions"], f"{name}.versions").items():
        field = f"components.{name}.versions.{key}"
        version(key, field)
        data = fields(entry, {"requires"} & set(mapping(entry, field)), field)
        requires = mapping(data.get("requires", {}), f"{field}.requires")
        for other, minimum in requires.items():
            if other not in known:
                raise GatesealError(
                    "invalid_field",
                    f"{field}.requires requires an unknown component {other!r}.",
                )
            version(minimum, f"{field}.requires.{other}")
        if name in requires:
            raise GatesealError(
                "invalid_field", f"{field} cannot require its own component."
            )
        reviewed[key] = cast(dict[str, str], requires)
    pin = string(table["pin"], f"components.{name}.pin")
    if pin not in reviewed:
        raise GatesealError(
            "invalid_field",
            f"components.{name}.pin {pin} is not one of its reviewed versions.",
        )
    return Catalog(pin, reviewed)


def require_compatible(
    selected: Mapping[str, Release],
    code: str,
    subject: str,
    next_step: str = "",
) -> None:
    """Require every selected release's requirements of the others to hold.

    selected holds the tool releases one selection uses, by tool name;
    requirements of tools it lacks, such as Betterleaks without secrets, do
    not apply. The failure names both releases.
    """
    for owner, release in sorted(selected.items()):
        for name, minimum in release.requires.items():
            if name not in selected:
                continue
            found = selected[name].version
            if version_key(found) < version_key(minimum):
                raise GatesealError(
                    code,
                    f"{owner} {release.version} needs {name} {minimum} or newer, "
                    f"but the {subject} select {name} {found}.",
                    next_step=next_step,
                )


def _exposed(value: object, toolset: Toolset) -> dict[tuple[str, str], ChoiceValue]:
    """Validate the choices a policy exposes, each with its default.

    A choice that a tool definition declares renders a key into the tool's
    configuration, so the policy's own file must not set that key too. Every
    alternative needs a default, which says which hook runs.
    """
    declared = declared_choices(
        (
            (use.capability, toolset.definitions[name])
            for name, use in toolset.uses.items()
        ),
        toolset.alternatives,
    )
    exposed: dict[tuple[str, str], ChoiceValue] = {}
    for capability, table in mapping(value, "choices").items():
        for name, declaration in mapping(table, f"choices.{capability}").items():
            field = f"choices.{capability}.{name}"
            if capability not in toolset.capabilities:
                raise _undefined_choice(capability, name)
            parse = choice_parser(declared, capability, name)
            default = fields(declaration, {"default"}, field)["default"]
            exposed[capability, name] = parse(default, field + ".default")
    for capability, name in toolset.alternatives:
        if (capability, name) not in exposed:
            raise GatesealError(
                "invalid_field",
                f"capabilities.{capability}.alternatives.{name} needs "
                f"choices.{capability}.{name} with the default hook.",
            )
    _require_unset_keys(exposed, toolset)
    return exposed


def _require_unset_keys(
    exposed: Mapping[tuple[str, str], ChoiceValue], toolset: Toolset
) -> None:
    """Require no tool's configuration to set a key that an exposed choice renders."""
    for tool, use in toolset.uses.items():
        for name, choice in toolset.definitions[tool].choices.items():
            if (
                (use.capability, name) in exposed
                and use.configuration is not None
                and choice.key in parse_toml(use.configuration)
            ):
                raise GatesealError(
                    "invalid_field",
                    f"The {tool} configuration sets {choice.key}, which "
                    f"choices.{use.capability}.{name} sets for each repository.",
                )


def load_policy_stream(data: bytes) -> Policy:
    """Load a policy from its content stream, as a store keeps it."""
    try:
        assets = parse_content_stream(data)
    except (ValueError, UnicodeError) as exc:
        raise GatesealError(
            "invalid_field", f"Unreadable policy content: {exc}."
        ) from exc

    def read_asset(name: str) -> bytes:
        if name not in assets:
            raise FileNotFoundError(name)
        return assets[name]

    return load_policy(read_asset)


def load_policy_tree(root: Path, *, compiler_version: str = __version__) -> Policy:
    return load_policy(
        lambda name: read(root, name, non_executable=True), compiler=compiler_version
    )


def bundled_root() -> Path:
    """Return the directory of the bundled reference policy."""
    root = files("gateseal").joinpath("data", "reference")
    if not isinstance(root, Path):
        raise GatesealError(
            "unsupported_resource", "Install the wheel as filesystem-backed resources."
        )
    return root.resolve()


def load_bundled_policy() -> Policy:
    return load_policy_tree(bundled_root())
