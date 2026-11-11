"""Edit policy.toml in place for gateseal policy add and pin.

People write policy.toml, so an edit keeps every other byte: comments, order
and line endings. Python reads TOML but cannot write it, so each edit changes
the lines it finds and then proves itself: the edited file must parse to
exactly the document the change intends. An edit it cannot prove, such as in
a file that writes its versions as an inline table, fails and writes nothing.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from gateseal.diagnostics import GatesealError
from gateseal.policy import POLICY
from gateseal.validation import parse_toml, version_key

LINE = re.compile(r"[^\n]*\n|[^\n]+\Z")

STRING = re.compile(r"""\s*("(?:[^"\\\n]|\\.)*"|'[^'\n]*').*""", re.DOTALL)
"""A one-line TOML string value and the rest of its line. A version needs no
escape in either quote."""


@dataclass(frozen=True)
class Table:
    """A table of a TOML file by its header's key path, and the lines it spans.

    start counts the comment lines directly above the header; end is past its
    last line that is not blank.
    """

    path: tuple[str, ...]
    start: int
    end: int


def _path(document: object) -> tuple[str, ...]:
    """Return the keys that lead to a parsed one-key line's innermost value."""
    path: list[str] = []
    while isinstance(document, dict) and len(cast(dict[str, object], document)) == 1:
        key, document = next(iter(cast(dict[str, object], document).items()))
        path.append(key)
    return tuple(path)


def _header(line: str) -> tuple[str, ...] | None:
    """Return a [table] header line's key path, () for [[array]], else None."""
    text = line.strip()
    if text.startswith("[["):
        return ()
    if not text.startswith("["):
        return None
    try:
        return _path(parse_toml(text.encode()))
    except GatesealError:
        return None


def _assignment(line: str) -> tuple[tuple[str, ...], int] | None:
    """Return a key = value line's key path and the index of its =, else None."""
    quote = ""
    for index, char in enumerate(line):
        if quote:
            quote = "" if char == quote else quote
        elif char in "\"'":
            quote = char
        elif char == "#":
            return None
        elif char == "=":
            try:
                return _path(parse_toml(line[:index].encode() + b"= 0")), index
            except GatesealError:
                return None
    return None


def _start(lines: list[str], header: int) -> int:
    """Return the first of the comment lines directly above header, else header."""
    while header and lines[header - 1].lstrip().startswith("#"):
        header -= 1
    return header


def _end(lines: list[str], start: int, end: int) -> int:
    """Return end moved back past the blank lines between start and it."""
    # pytestmut: off[cmp.boundary] -- a table's first line is never blank
    while end > start and not lines[end - 1].strip():
        # pytestmut: on
        end -= 1
    return end


def tables(lines: list[str]) -> list[Table]:
    headers = [
        (index, path)
        for index, line in enumerate(lines)
        if (path := _header(line)) is not None
    ]
    starts = [_start(lines, index) for index, _ in headers]
    tables: list[Table] = []
    for position, (_, path) in enumerate(headers):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        tables.append(Table(path, starts[position], _end(lines, starts[position], end)))
    return tables


def _proven(
    data: bytes,
    lines: list[str],
    tool: str,
    change: Callable[[dict[str, object]], None],
    failure: GatesealError,
) -> bytes:
    """Return the edited lines, which must parse to data's document after change.

    change edits the parsed table of tool's component.
    """
    expected = parse_toml(data)
    components = cast(dict[str, object], expected["components"])
    change(cast(dict[str, object], components[tool]))
    edited = "".join(lines)
    try:
        if parse_toml(edited.encode("utf-8")) == expected:
            return edited.encode("utf-8")
    except GatesealError:
        pass
    raise failure


def _inserted(lines: list[str], at: int, added: list[str], newline: str) -> list[str]:
    """Return lines with added at at, a blank line apart from the text around it."""
    before, after = lines[:at], lines[at:]
    if before and not before[-1].endswith("\n"):
        before[-1] += newline
    above = [newline] if before and before[-1].strip() else []
    below = [newline] if after and after[0].strip() else []
    return [*before, *above, *added, *below, *after]


def _component_tables(lines: list[str], tool: str) -> tuple[Table | None, list[Table]]:
    """Return tool's component table and its version tables in file order.

    Each version table spans its subtables.
    """
    blocks: dict[str, Table] = {}
    component = None
    for table in tables(lines):
        if table.path[:3] == ("components", tool, "versions") and len(table.path) > 3:
            first = blocks.get(table.path[3], table)
            # pytestmut: off[num.shift] -- only path[3] is read
            blocks[table.path[3]] = Table(table.path[:4], first.start, table.end)
            # pytestmut: on
        elif table.path == ("components", tool):
            component = table
    return component, sorted(blocks.values(), key=lambda block: block.start)


def _version_position(lines: list[str], tool: str, version: str) -> int:
    """Return the line where the table that reviews tool's version goes."""
    component, ordered = _component_tables(lines, tool)
    keys = [version_key(block.path[3]) for block in ordered]
    # pytestmut: off[cmp.boundary] -- an existing version fails the proof
    older = [b for b in ordered if version_key(b.path[3]) < version_key(version)]
    # pytestmut: on
    if not ordered:
        return component.end if component else len(lines)
    if keys == sorted(keys, reverse=True):
        return older[0].start if older else ordered[-1].end
    return older[-1].end if older else ordered[0].start


def reviewed(data: bytes, tool: str, version: str) -> bytes:
    """Return policy.toml with an empty table that reviews tool's version.

    The table goes beside the component's other versions and keeps their
    order: they run newest first as init writes them, or oldest first.
    """
    lines = LINE.findall(data.decode("utf-8"))
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    header = f"[components.{tool}.versions.{json.dumps(version)}]{newline}"
    at = _version_position(lines, tool, version)
    lines = _inserted(lines, at, [header], newline)

    def change(table: dict[str, object]) -> None:
        cast(dict[str, object], table["versions"])[version] = {}

    return _proven(
        data,
        lines,
        tool,
        change,
        GatesealError(
            "policy_edit_unsupported",
            f"gateseal policy add cannot review {tool} {version} in {POLICY} as it "
            "is written.",
            next_step=f'Add [components.{tool}.versions."{version}"] to {POLICY} '
            "yourself, then run gateseal policy lock.",
        ),
    )


def pinned(data: bytes, tool: str, version: str) -> bytes:
    """Return policy.toml with tool's pin set to version where it is written."""
    lines = LINE.findall(data.decode("utf-8"))
    table: tuple[str, ...] = ()
    for index, line in enumerate(lines):
        header = _header(line)
        if header is not None:
            table = header
            continue
        found = _assignment(line)
        if found is None or table + found[0] != ("components", tool, "pin"):
            continue
        value = STRING.fullmatch(line, found[1] + 1)
        if value is not None:
            quote = value[1][0]
            lines[index] = (
                line[: value.start(1)] + quote + version + quote + line[value.end(1) :]
            )
        break

    def change(table: dict[str, object]) -> None:
        table["pin"] = version

    return _proven(
        data,
        lines,
        tool,
        change,
        GatesealError(
            "policy_edit_unsupported",
            f"gateseal policy pin cannot set the pin of {tool} in {POLICY} as it "
            "is written.",
            next_step=f'Set pin = "{version}" under [components.{tool}] in '
            f"{POLICY} yourself, then run gateseal policy check.",
        ),
    )


def component(data: bytes, name: str, version: str) -> bytes:
    """Return policy.toml with a new component name that pins and reviews version.

    Its tables go after the last component's, or at the end of the file.
    """
    lines = LINE.findall(data.decode("utf-8"))
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    found = [t.end for t in tables(lines) if t.path[:1] == ("components",)]
    at = found[-1] if found else len(lines)
    added = [
        f"[components.{name}]{newline}",
        f"pin = {json.dumps(version)}{newline}",
        newline,
        f"[components.{name}.versions.{json.dumps(version)}]{newline}",
    ]
    lines = _inserted(lines, at, added, newline)
    expected = parse_toml(data)
    components = cast(dict[str, object], expected.setdefault("components", {}))
    components[name] = {"pin": version, "versions": {version: {}}}
    edited = "".join(lines).encode("utf-8")
    try:
        if parse_toml(edited) == expected:
            return edited
    except GatesealError:
        pass
    raise GatesealError(
        "policy_edit_unsupported",
        f"gateseal policy add cannot add {name} {version} to {POLICY} as it is "
        "written.",
        next_step=f'Add [components.{name}] with pin = "{version}" and '
        f'[components.{name}.versions."{version}"] to {POLICY} yourself, then '
        "run gateseal policy lock.",
    )
