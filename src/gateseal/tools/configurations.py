"""The configuration formats: what a policy's rule file holds and what it renders to.

A definition's [configuration] names a format. The policy's file under rules/
is TOML in every format, so a Policy Author writes one syntax, with comments,
for every tool. The format says what review accepts in that file and what
configure writes to the owned output, .gateseal/<name>.<output>:

    format   owned output              choices
    toml     .gateseal/<name>.toml     rendered as top-level keys
    eslint   .gateseal/<name>.json     none

owned_outputs renders a tool's owned outputs: its fixed files and its
configuration. This module imports no tools module but choices at run time,
so model can import it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from gateseal.diagnostics import GatesealError
from gateseal.tools.choices import EXTEND
from gateseal.validation import canonical, mapping, nested, parse_toml, sequence, string

if TYPE_CHECKING:
    from gateseal.tools.model import Tool

BUILT_IN_RULES = frozenset({"useDefault", "disabledRules"})
"""The keys of a Betterleaks or Gitleaks extend table that start from the rules
built into the tool and turn some of them off, without loading a file."""

PYRIGHT_EXTENDS = ("tool", "pyright", "extends")
"""The key of a Pyright configuration that loads another file."""

PLUGINS = "plugins"
"""The key of a Prettier configuration, or of the options of one of its
overrides, that loads packages from the repository."""

ESLINT_KEYS = ("files", "parser", "plugins", "rules")
"""Every key of the ESLint data, a closed set: no other key can load a file,
name a preset or give type information."""

SEVERITIES = ("off", "warn", "error")
"""The severities a rule takes, as its value or as its array's first item."""

PREFIX = re.compile(
    r"(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*|@[a-z0-9][a-z0-9._-]*"
)
"""A plugin's prefix, as its rules' names start with it, such as
@typescript-eslint, react or the scoped @next/next."""


class ConfigurationFormat(StrEnum):
    TOML = "toml"
    ESLINT = "eslint"


@dataclass(frozen=True)
class Configuration:
    """A definition's configuration: its format and its owned output's path.

    path is .gateseal/<name>.<output of the format>, derived from the
    definition's name, so a definition cannot name a path gateseal does not
    own. The placeholder {configuration} names it.
    """

    format: ConfigurationFormat
    path: str


class Format(Protocol):
    """What one configuration format accepts and writes.

    output is the owned output's extension. takes_choices says whether a
    definition of this format may declare Policy Choices, which render into
    the output.
    """

    output: ClassVar[str]
    takes_choices: ClassVar[bool]

    def check(
        self,
        data: bytes,
        field: str,
        packages: Mapping[str, str] | None,
        pinned_at: str,
    ) -> None:
        """Refuse a policy file this format cannot write safely, with invalid_field.

        field names the file in a failure, such as "The eslint configuration".
        packages maps each npm package that policy.toml pins for the tool, at
        pinned_at, to its version. It is None for a tool that does not run on
        Node, which loads no npm package.
        """
        ...

    def render(self, data: bytes, keys: Mapping[str, str]) -> bytes:
        """Return the owned output of a checked policy file.

        keys maps each chosen choice's configuration key to its value, in
        definition order; it is empty for a format that takes no choices.
        """
        ...


def _parse_lf_toml(data: bytes, field: str) -> dict[str, object]:
    """Parse a policy's TOML file, which has LF line endings.

    Owned outputs are compared with line endings normalized to LF, so a file
    with a carriage return could never match its own output.
    """
    if b"\r" in data:
        raise GatesealError(
            "invalid_field", f"{field} must use LF line endings, without CR."
        )
    return parse_toml(data)


@dataclass(frozen=True)
class TomlFormat:
    """The policy's TOML, copied after the selection's choice keys.

    A configuration that loads another file would escape review: a top-level
    extend, or Pyright's extends in [tool.pyright]. An extend table that only
    selects among the rules built into the tool loads none, and the Lock's
    checksum of the tool covers those rules. For a tool on Node, Prettier's
    plugins, at the top level or in an override's options, load packages that
    no policy pins. For any other tool, plugins is a key like any other.
    """

    output: ClassVar[str] = "toml"
    takes_choices: ClassVar[bool] = True

    def check(
        self,
        data: bytes,
        field: str,
        packages: Mapping[str, str] | None,
        pinned_at: str,
    ) -> None:
        document = _parse_lf_toml(data, field)
        extend = document.get(EXTEND)
        if extend is not None and not (
            isinstance(extend, dict) and extend.keys() <= BUILT_IN_RULES
        ):
            raise GatesealError(
                "invalid_field",
                f"{field} must not extend another configuration. A Betterleaks or "
                "Gitleaks extend table may hold only useDefault and disabledRules.",
            )
        if nested(document, *PYRIGHT_EXTENDS) is not None:
            raise GatesealError(
                "invalid_field",
                f"{field} must not extend another configuration: remove "
                f"{'.'.join(PYRIGHT_EXTENDS)}.",
            )
        if packages is not None and _names_plugins(document):
            raise GatesealError(
                "invalid_field",
                f"{field} must not name plugins, also not in the options of an "
                "override: Prettier loads a plugin from the repository, where no "
                "policy pins it.",
            )

    def render(self, data: bytes, keys: Mapping[str, str]) -> bytes:
        """Return each key as key = "value" on its own line, then the policy's bytes.

        So a choice stays a top-level key whatever tables the policy declares.
        """
        lines = "".join(f'{key} = "{value}"\n' for key, value in keys.items())
        return lines.encode() + data


def _names_plugins(document: Mapping[str, object]) -> bool:
    """Say whether a configuration or the options of an override hold PLUGINS."""
    overrides = document.get("overrides")
    listed = cast(list[object], overrides) if isinstance(overrides, list) else []
    options = [document, *(nested(item, "options") for item in listed)]
    return any(isinstance(item, dict) and PLUGINS in item for item in options)


@dataclass(frozen=True)
class EslintFormat:
    """ESLint's data: the policy's TOML, checked and written as canonical JSON.

    The policy's file holds exactly these keys:

    - files: the patterns of the files ESLint checks, from the repository root.
    - parser (optional): an npm package that the tool's packages pin.
    - plugins (optional): a table that maps each rule prefix to an npm package
      that the tool's packages pin.
    - rules: a table that maps each rule to off, warn or error, or to an array
      of one of them and then the rule's options. A rule named <prefix>/<rule>
      needs that prefix in plugins.

    Any other key fails, extends, languageOptions and settings among them, so
    no key loads another file, names a preset or gives type information. A
    value that JSON cannot hold, such as a date or an infinite number, fails
    too. The loader of the eslint definition reads the output and resolves
    the parser and the plugins from the packages that npm installed
    beside ESLint.
    """

    output: ClassVar[str] = "json"
    takes_choices: ClassVar[bool] = False

    def check(
        self,
        data: bytes,
        field: str,
        packages: Mapping[str, str] | None,
        pinned_at: str,
    ) -> None:
        document = _parse_lf_toml(data, field)
        packages = packages or {}
        _require_keys(document, field)
        patterns = sequence(document["files"], f"{field}'s files")
        if not patterns or not all(isinstance(item, str) and item for item in patterns):
            raise GatesealError(
                "invalid_field", f"{field}'s files must list one or more patterns."
            )
        if "parser" in document:
            parser = string(document["parser"], f"{field}'s parser")
            _pinned(f"{field} names the parser {parser}", parser, packages, pinned_at)
        plugins = mapping(document.get("plugins", {}), f"{field}'s plugins")
        for prefix, value in plugins.items():
            name = string(value, f"{field}'s plugins.{prefix}")
            if not PREFIX.fullmatch(prefix):
                raise GatesealError(
                    "invalid_field",
                    f"{field} declares the plugin prefix {prefix!r}, but a prefix "
                    "is a rule name's start, such as @typescript-eslint or react.",
                )
            subject = f"{field} names the plugin {name} for {prefix}"
            _pinned(subject, name, packages, pinned_at)
        for rule, value in mapping(document["rules"], f"{field}'s rules").items():
            _rule(rule, value, plugins, field)

    def render(self, data: bytes, keys: Mapping[str, str]) -> bytes:
        """Return the data as canonical JSON, with plugins always present.

        So the loader reads one shape. Keys are sorted, which ESLint ignores
        for rules and plugins, and arrays keep their order.
        """
        document: dict[str, object] = {"plugins": {}}
        return canonical(document | parse_toml(data))


def _require_keys(document: Mapping[str, object], field: str) -> None:
    """Require files and rules, and no key outside ESLINT_KEYS."""
    for key in document:
        if key not in ESLINT_KEYS:
            raise GatesealError(
                "invalid_field",
                f"{field} sets {key}, but it holds only "
                f"{', '.join(ESLINT_KEYS[:-1])} and {ESLINT_KEYS[-1]}, so no key "
                "loads another file.",
            )
    for key in ("files", "rules"):
        if key not in document:
            raise GatesealError("invalid_field", f"{field} needs {key}.")


def _pinned(subject: str, name: str, packages: Mapping[str, str], at: str) -> None:
    """Require a package that the tool's packages pin, so npm installs it."""
    if name not in packages:
        raise GatesealError("invalid_field", f"{subject}, which {at} does not pin.")


def _rule(name: str, value: object, plugins: Mapping[str, object], field: str) -> None:
    """Require a rule of a declared plugin, with a severity and JSON options."""
    prefix = name.rpartition("/")[0]
    if prefix and prefix not in plugins:
        raise GatesealError(
            "invalid_field",
            f"{field} sets the rule {name}, but its plugins declare no {prefix}.",
        )
    listed = cast(list[object], value) if isinstance(value, list) else [value]
    if not listed or listed[0] not in SEVERITIES:
        raise GatesealError(
            "invalid_field",
            f"{field} sets the rule {name} without a severity; a rule takes off, "
            "warn or error, then its options.",
        )
    _json(listed, f"{field}'s rules.{name}")


def _json(value: object, at: str) -> None:
    """Require a value that JSON holds: strings, numbers, booleans, arrays, tables."""
    if isinstance(value, dict):
        value = list(cast(dict[str, object], value).values())
    if isinstance(value, list):
        for item in cast(list[object], value):
            _json(item, at)
        return
    finite = not isinstance(value, float) or math.isfinite(value)
    if not (isinstance(value, str | int | float) and finite):
        raise GatesealError(
            "invalid_field",
            f"{at} holds {value}, which JSON cannot hold; ESLint data holds "
            "strings, finite numbers, booleans, arrays and tables.",
        )


FORMATS: Mapping[ConfigurationFormat, Format] = {
    ConfigurationFormat.TOML: TomlFormat(),
    ConfigurationFormat.ESLINT: EslintFormat(),
}
"""Each format's rules, by its value of configuration.format."""


def owned_outputs(tool: Tool, chosen: Mapping[str, object]) -> dict[str, bytes]:
    """Return the owned outputs a tool's definition declares, by path.

    They are each fixed file and the policy's configuration as its format
    renders it at the definition's configuration path. chosen holds the
    selection's choices of the tool's capability; each of the definition's
    choices that chosen holds goes to the format by its key, in definition
    order.
    """
    outputs = dict(tool.definition.files)
    configuration = tool.definition.configuration
    # pytestmut: off[bool.and_or] -- a use has one when its definition does
    if configuration is not None and tool.use.configuration is not None:
        # pytestmut: on
        keys = {
            choice.key: str(chosen[name])
            for name, choice in tool.definition.choices.items()
            if name in chosen
        }
        outputs[configuration.path] = FORMATS[configuration.format].render(
            tool.use.configuration, keys
        )
    return outputs
