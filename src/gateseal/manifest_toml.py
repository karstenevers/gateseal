"""Edit gateseal.toml in place for gateseal upgrade.

A Repository Maintainer writes the Manifest, so an upgrade keeps every byte it
does not own, as policy_toml does for policy.toml. It owns [policy] and the
[exceptions.<component>] table of each component it targets. It replaces such
a table from its header to its last line and keeps the comments above it, or
removes the table with those comments. Then it proves the result: the edited
file must parse to exactly the Manifest the upgrade intends. A Manifest it
cannot edit that way, such as one that writes an exception as an inline table,
fails and writes nothing.
"""

import json
from collections.abc import Mapping
from typing import cast

from gateseal.bootstrap import MANIFEST
from gateseal.diagnostics import GatesealError
from gateseal.manifest import ExceptionRequest, Source, policy_lines
from gateseal.policy_toml import LINE, Table, tables
from gateseal.validation import parse_toml

Edit = tuple[int, int, list[str]]
"""Lines start to end of a file, and the lines without newlines that replace them."""


def _header(lines: list[str], table: Table) -> int:
    """Return the index of table's header line, below its comments."""
    index = table.start
    while lines[index].lstrip().startswith("#"):
        index += 1
    return index


def _exception_lines(request: ExceptionRequest) -> list[str]:
    return [
        f"[exceptions.{request.component}]",
        f"version = {json.dumps(request.version)}",
        f"reason = {json.dumps(request.reason, ensure_ascii=False)}",
        f"review_by = {request.review_by.isoformat()}",
    ]


def _insertion(
    lines: list[str], found: Mapping[tuple[str, ...], Table], new: list[list[str]]
) -> list[Edit]:
    """Return the edit that adds each new table after the [exceptions] tables.

    The tables keep their order, and go at the end of a file that has no
    [exceptions] table. No new table makes no edit.
    """
    ends = [t.end for t in found.values() if t.path[:1] == ("exceptions",)]
    at = max(ends, default=len(lines))
    added = [line for table in new for line in ("", *table)]
    return [(at, at, added)] if added else []


def _exception_edits(
    lines: list[str],
    found: Mapping[tuple[str, ...], Table],
    exceptions: Mapping[str, ExceptionRequest | None],
    listed: Mapping[str, object],
) -> tuple[list[Edit], dict[str, object]]:
    """Return the edits that give each component of exceptions its table.

    listed is the Manifest's parsed exceptions, and the edits come back with
    a copy of it that has the same changes.
    """
    changed = dict(listed)
    edits: list[Edit] = []
    new: list[list[str]] = []
    for name, request in exceptions.items():
        table = found.get(("exceptions", name))
        if request is None:
            changed.pop(name, None)
            if table is not None:
                edits.append((table.start, table.end, []))
            continue
        rendered = _exception_lines(request)
        parsed = parse_toml("\n".join(rendered).encode())["exceptions"]
        changed[name] = cast(dict[str, object], parsed)[name]
        if table is not None:
            edits.append((_header(lines, table), table.end, rendered))
        else:
            new.append(rendered)
    if edits and not changed and ("exceptions",) not in found:
        # Every edit removes a table, and the last [exceptions.<component>]
        # also made the implicit parent.
        start, end, _ = edits[0]
        edits[0] = (start, end, ["[exceptions]"])
    return edits + _insertion(lines, found, new), changed


def _widened(lines: list[str], start: int, end: int) -> tuple[int, int]:
    """Return a removed table's span with the blank lines beside it.

    The span takes the blank lines below the table, and at the end of the
    file those above it too.
    """
    while end < len(lines) and not lines[end].strip():
        end += 1
    if end == len(lines):
        while start and not lines[start - 1].strip():
            start -= 1
    return start, end


def _applied(lines: list[str], edits: list[Edit], newline: str) -> bytes:
    """Make each edit in lines, from the last to the first, and return the file."""
    # pytestmut: off[num.shift] -- no two edits share a start and an end
    for start, end, rendered in sorted(edits, key=lambda edit: edit[:2], reverse=True):
        # pytestmut: on
        if not rendered:
            start, end = _widened(lines, start, end)
        if start and not lines[start - 1].endswith("\n"):
            lines[start - 1] += newline
        below = [""] if rendered and end < len(lines) and lines[end].strip() else []
        lines[start:end] = [line + newline for line in (*rendered, *below)]
    return "".join(lines).encode("utf-8")


def edited(
    data: bytes,
    source: Source | None,
    exceptions: Mapping[str, ExceptionRequest | None],
) -> bytes:
    """Return the Manifest with source's [policy] and each exception changed.

    source None keeps [policy]. exceptions maps a component to the exception
    it gets, or to None to remove its exception. New exception tables go
    after the last [exceptions] table, in the order exceptions lists them.
    """
    lines = LINE.findall(data.decode("utf-8"))
    newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
    expected = parse_toml(data)
    found = {table.path: table for table in tables(lines)}
    edits: list[Edit] = []
    if source is not None:
        rendered = policy_lines(source)
        expected["policy"] = parse_toml("\n".join(rendered).encode())["policy"]
        table = found.get(("policy",))
        if table is not None:
            edits.append((_header(lines, table), table.end, rendered))
    listed = cast(dict[str, object], expected.get("exceptions", {}))
    exception_edits, listed = _exception_edits(lines, found, exceptions, listed)
    edits += exception_edits
    if listed or "exceptions" in expected:
        expected["exceptions"] = listed
    return _checked(_applied(lines, edits, newline), expected)


def _checked(result: bytes, expected: dict[str, object]) -> bytes:
    """Return result if it parses to expected, or refuse the edit."""
    try:
        if parse_toml(result) == expected:
            return result
    except GatesealError:
        pass
    raise GatesealError(
        "manifest_edit_unsupported",
        f"gateseal upgrade cannot change {MANIFEST} as it is written.",
        next_step=f"Write [policy] and each [exceptions.<component>] in {MANIFEST} "
        "as a table with one key per line, or make the change yourself, then run "
        "gateseal configure.",
    )
