"""Edit policy.toml in place for gateseal policy add and pin.

People write policy.toml, so an edit keeps every other byte: comments, order
and line endings. Python reads TOML but cannot write it, so each edit changes
the lines it finds and then proves itself: the edited file must parse to
exactly the document the change intends. An edit it cannot prove, such as in
a file that writes a tool as an inline table, fails and writes nothing.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from gateseal.diagnostics import GatesealError
from gateseal.policy import POLICY
from gateseal.validation import parse_toml, version_key

LINE = re.compile(r"[^\n]*\n|[^\n]+\Z")

STRING = re.compile(r"""\s*("(?:[^"\\\n]|\\.)*"|'[^'\n]*')""")
"""A one-line TOML string value. A version needs no escape in either quote."""


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


def _tool_path(data: bytes, tool: str) -> tuple[str, ...]:
    """Return the key path of tool's table in a policy that review_policy accepts."""
    capabilities = cast(dict[str, dict[str, object]], parse_toml(data)["capabilities"])
    [capability] = [
        name
        for name, table in capabilities.items()
        if tool in cast(dict[str, object], table["tools"])
    ]
    return ("capabilities", capability, "tools", tool)


def _value_start(lines: list[str], path: tuple[str, ...]) -> int | None:
    """Return the offset in the joined lines where the value of key path starts.

    The key may be written under its table's header or as a dotted key under
    a parent table's. None means no line assigns it.
    """
    table: tuple[str, ...] = ()
    offset = 0
    for line in lines:
        header = _header(line)
        if header is not None:
            table = header
        elif (found := _assignment(line)) is not None and table + found[0] == path:
            return offset + found[1] + 1
        offset += len(line)
    return None


def _proven(
    data: bytes,
    text: str,
    path: tuple[str, ...],
    change: Callable[[dict[str, object]], None],
    failure: GatesealError,
) -> bytes:
    """Return text, which must parse to data's document after change.

    change edits the parsed table at path.
    """
    expected = parse_toml(data)
    table = expected
    for key in path:
        table = cast(dict[str, object], table[key])
    change(table)
    try:
        if parse_toml(text.encode("utf-8")) == expected:
            return text.encode("utf-8")
    except GatesealError:
        pass
    raise failure


def _items(text: str, start: int) -> list[re.Match[str]] | None:
    """Return the strings of the one-level array whose value starts at start.

    None means a value that is not such an array, or one that holds anything
    but strings and comments, so the edit fails its proof.
    """
    opened = re.compile(r"\s*\[").match(text, start)
    if opened is None:
        return None
    items: list[re.Match[str]] = []
    position = opened.end()
    while (token := ARRAY.match(text, position)) is not None:
        if token["close"] is not None:
            return items
        if token["string"] is not None:
            items.append(token)
        position = token.end()
    return None


ARRAY = re.compile(
    r"""\s*(?:(?P<string>"[^"\\\n]*"|'[^'\n]*')|(?P<close>\])|,|\#[^\n]*)"""
)
"""One token of a one-level array of strings: a string, its end, a comma or a
comment. A version needs no escape in either quote."""


def _position(listed: list[str], version: str) -> int:
    """Return where version goes among listed versions, which keep their order.

    They run newest first as init writes them, or oldest first.
    """
    keys = [version_key(item) for item in listed]
    new = version_key(version)
    older = [
        n
        for n, key in enumerate(keys)
        if key < new  # pytestmut: skip[cmp.boundary] -- add skips reviewed versions
    ]
    if keys == sorted(keys, reverse=True):
        return older[0] if older else len(listed)
    return older[-1] + 1 if older else 0


def _insert(text: str, items: list[re.Match[str]], version: str, newline: str) -> str:
    """Return text with version among the array's items, at its position.

    The new string takes its neighbor's quote. It takes its own line when the
    neighbor has one, and the neighbor before it ends that line with a comma.
    """
    index = _position([item["string"][1:-1] for item in items], version)
    neighbor = items[min(index, len(items) - 1)]
    quote = neighbor["string"][0]
    added = f"{quote}{version}{quote}"
    start, end = neighbor.span("string")
    line = text[:start].rfind("\n") + 1
    indent = text[line:start]
    own = not indent.strip()
    if index < len(items):
        if own:
            return f"{text[:line]}{indent}{added},{newline}{text[line:]}"
        return f"{text[:start]}{added}, {text[start:]}"
    after = text.find("\n", end) + 1
    if own and after and text[end:after].lstrip().startswith(","):
        return f"{text[:after]}{indent}{added},{newline}{text[after:]}"
    return f"{text[:end]}, {added}{text[end:]}"


def reviewed(data: bytes, tool: str, version: str) -> bytes:
    """Return policy.toml with version in tool's list of Reviewed Versions."""
    text = data.decode("utf-8")
    lines = LINE.findall(text)
    newline = "\r\n" if lines[0].endswith("\r\n") else "\n"
    path = _tool_path(data, tool)
    start = _value_start(lines, (*path, "reviewed"))
    items = _items(text, start) if start is not None else None
    edited = _insert(text, items, version, newline) if items else text

    def change(table: dict[str, object]) -> None:
        listed = cast(list[str], table["reviewed"])
        listed.insert(_position(listed, version), version)

    table = ".".join(path)
    return _proven(
        data,
        edited,
        path,
        change,
        GatesealError(
            "policy_edit_unsupported",
            f"gateseal policy add cannot review {tool} {version} in {POLICY} as it "
            "is written.",
            next_step=f'Add "{version}" to reviewed under [{table}] in {POLICY} '
            "yourself, then run gateseal policy lock.",
        ),
    )


def pinned(data: bytes, tool: str, version: str) -> bytes:
    """Return policy.toml with tool's pin set to version where it is written."""
    text = data.decode("utf-8")
    path = _tool_path(data, tool)
    start = _value_start(LINE.findall(text), (*path, "pin"))
    value = STRING.match(text, start) if start is not None else None
    edited = text
    if value is not None:
        quote = value[1][0]
        edited = text[: value.start(1)] + quote + version + quote + text[value.end(1) :]

    def change(table: dict[str, object]) -> None:
        table["pin"] = version

    table = ".".join(path)
    return _proven(
        data,
        edited,
        path,
        change,
        GatesealError(
            "policy_edit_unsupported",
            f"gateseal policy pin cannot set the pin of {tool} in {POLICY} as it "
            "is written.",
            next_step=f'Set pin = "{version}" under [{table}] in {POLICY} '
            "yourself, then run gateseal policy check.",
        ),
    )
