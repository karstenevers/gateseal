"""The presets of an ESLint rule file, as the Policy Lock records them.

gateseal policy lock reads a preset once: npm installs the version's lockfile
and PROGRAM runs on the policy's Node. Every other command reads the record
offline. A version's record maps each package to the blocks of each of its
presets, keyed by what they were read from, so a prefix that the rule file
maps to another package makes the record stale. A record is kept exactly
while its lockfile is kept: a new lockfile reads every preset again.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from gateseal.diagnostics import GatesealError
from gateseal.tools.configurations import (
    ConfigurationFormat,
    Preset,
    Presets,
    named_presets,
    setting,
)
from gateseal.tools.model import ToolDefinition, ToolUse, Upstream, lock_failure
from gateseal.validation import fields, mapping, nested, sequence, string

PRESETS = "presets"
"""The key of a version's Policy Lock record, and of a rule file, that holds presets."""

PROGRAM = Path(__file__).with_name("eslint_presets.mjs")
"""The ES module that reads presets from an install; its header gives its contract."""


def lock(
    definition: ToolDefinition,
    use: ToolUse,
    version: str,
    lockfile: object,
    recorded: object,
    upstream: Upstream,
) -> dict[str, dict[str, object]]:
    """Return the presets record of one reviewed version, empty when none is named.

    recorded is the version's record to keep, or None. Only a preset that it
    lacks is read; a rerun with nothing changed reads none, so it needs
    neither npm nor Node.
    """
    presets, plugins = _rule_file(definition, use)
    missing: dict[str, list[str]] = {}
    for preset in presets:
        if nested(recorded, preset.package, preset.config) is None:
            missing.setdefault(preset.package, []).append(preset.config)
    found: object = None
    if missing:
        found = upstream.evaluate(
            mapping(lockfile, "lockfile"),
            PROGRAM,
            {"plugins": plugins, "presets": missing},
            definition.name,
            version,
        )
    record: dict[str, dict[str, object]] = {}
    field = f"components.{definition.name}.versions.{version}.{PRESETS}"
    for preset in presets:
        blocks = nested(recorded, preset.package, preset.config)
        if blocks is None:
            held = nested(lockfile, "packages", f"node_modules/{preset.package}")
            subject = f"{preset.package} {nested(held, 'version')}"
            blocks = _resolved(
                definition.name, preset, subject, nested(found, preset.package)
            )
        at = f"{field}.{preset.package}.{preset.config}"
        undeclared = _undeclared(_blocks(blocks, at), plugins)
        if undeclared is not None:
            rule, prefix = undeclared
            raise GatesealError(
                "invalid_field",
                f"The {definition.name} configuration names the preset {preset.name}, "
                f"which turns on the rule {rule}, but its plugins declare no {prefix}.",
                next_step=f"Declare {prefix} in plugins, or remove {preset.name} "
                "from presets. Then run gateseal policy lock.",
            )
        record.setdefault(preset.package, {})[preset.config] = blocks
    return record


def read(
    definition: ToolDefinition, use: ToolUse, version: str, record: object
) -> Presets:
    """Return the blocks of the presets that the rule file names, in its order.

    record is the version's presets record, or None. A record of other
    presets, or one that turns on a rule of a plugin the rule file does not
    declare, fails with policy_lock_stale. A block that is not ESLint data
    fails with invalid_field, which load_policy reports as policy_lock_edited.
    """
    presets, plugins = _rule_file(definition, use)
    field = f"components.{definition.name}.versions.{version}.{PRESETS}"
    found = mapping({} if record is None else record, field)
    recorded = {
        package: sorted(mapping(configs, f"{field}.{package}"))
        for package, configs in found.items()
    }
    named: dict[str, list[str]] = {}
    for preset in presets:
        named[preset.package] = sorted({*named.get(preset.package, ()), preset.config})
    subject = f"{definition.name} {version}"
    if recorded != named:
        raise lock_failure(
            "policy_lock_stale",
            f"policy.lock records the presets {json.dumps(recorded)} "
            f"for {subject}, but its configuration names "
            f"{json.dumps(named, sort_keys=True)}.",
        )
    blocks: list[Mapping[str, object]] = []
    for preset in presets:
        at = f"{field}.{preset.package}.{preset.config}"
        own = _blocks(nested(found, preset.package, preset.config), at)
        undeclared = _undeclared(own, plugins)
        if undeclared is not None:
            raise lock_failure(
                "policy_lock_stale",
                f"policy.lock records the preset {preset.name} of {subject} with the "
                f"rule {undeclared[0]}, but its configuration's plugins declare no "
                f"{undeclared[1]}.",
            )
        blocks.extend(own)
    return tuple(blocks)


def _rule_file(
    definition: ToolDefinition, use: ToolUse
) -> tuple[tuple[Preset, ...], dict[str, str]]:
    """Return the presets and plugins of the tool's ESLint rule file.

    A tool of another format, or without a configuration, names none.
    """
    configuration = definition.configuration
    if configuration is None or configuration.format is not ConfigurationFormat.ESLINT:
        return (), {}
    return named_presets(cast(bytes, use.configuration))


def _resolved(tool: str, preset: Preset, package: str, found: object) -> object:
    """Return the blocks that PROGRAM read for preset, or refuse the preset.

    package names the preset's package at its locked version, and found is
    what PROGRAM printed for it.
    """
    subject = f"The {tool} configuration names the preset {preset.name}"
    read = nested(found, "presets", preset.config)
    if read is None:
        names = sequence(nested(found, "configs"), f"{preset.package}'s presets")
        listed = sorted({str(name).removeprefix("flat/") for name in names})
        raise GatesealError(
            "invalid_field",
            f"{subject}, but {package} has no preset {preset.config}. It has "
            f"{', '.join(listed) or 'none'}.",
            next_step="Correct the name in presets, then run gateseal policy lock.",
        )
    typed = sequence(nested(read, "typed"), f"{preset.name}'s typed rules")
    if typed:
        raise GatesealError(
            "invalid_field",
            f"{subject}, whose rule {typed[0]} needs type information, which "
            "gateseal's ESLint data cannot give.",
            next_step=f"Remove {preset.name} from presets, or name a preset "
            "without type-checked rules.",
        )
    blocks = nested(read, "blocks")
    try:
        _blocks(blocks, f"The preset {preset.name}")
    except GatesealError as exc:
        raise GatesealError(
            "invalid_field",
            f"{subject}, which gateseal cannot record as ESLint data: {exc}",
            next_step=f"Remove {preset.name} from presets, or name a flat "
            f"configuration of {preset.package}.",
        ) from exc
    return blocks


def _blocks(value: object, field: str) -> Presets:
    """Return a preset's blocks: rules, and optionally files and ignores, as data.

    The same check runs on what PROGRAM read, at lock, and on the record, at load.
    """
    blocks: list[Mapping[str, object]] = []
    for index, item in enumerate(sequence(value, field)):
        at = f"{field}[{index}]"
        keys = {"rules"} | ({"files", "ignores"} & mapping(item, at).keys())
        block = fields(item, keys, at)
        _patterns(block.get("files", []), f"{at}.files", together=True)
        _patterns(block.get("ignores", []), f"{at}.ignores", together=False)
        for rule, value in mapping(block["rules"], f"{at}.rules").items():
            setting(rule, value, at)
        blocks.append(block)
    return tuple(blocks)


def _patterns(value: object, field: str, together: bool) -> None:
    """Require patterns: strings, and with together also arrays of strings."""
    for item in sequence(value, field):
        listed = (
            cast(list[object], item) if together and isinstance(item, list) else [item]
        )
        for pattern in listed:
            string(pattern, field)


def _undeclared(blocks: Presets, plugins: Mapping[str, str]) -> tuple[str, str] | None:
    """Return a rule that blocks turn on whose prefix plugins lack, and the prefix.

    ESLint skips a rule that is off, so an off rule of any plugin is harmless.
    """
    for block in blocks:
        for rule, value in cast(Mapping[str, object], block["rules"]).items():
            prefix = rule.rpartition("/")[0]
            severity = (
                cast(list[object], value)[0] if isinstance(value, list) else value
            )
            if prefix and prefix not in plugins and severity != "off":
                return rule, prefix
    return None
