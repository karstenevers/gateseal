"""Fail when a function's CRAP score is over the limit.

CRAP = complexity² × (1 − coverage)³ + complexity, per function. Coverage comes
from coverage.py's JSON report with branch coverage, complexity from `ast`.
"""

import argparse
import ast
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

type Definition = ast.FunctionDef | ast.AsyncFunctionDef

SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class InputError(Exception):
    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"{path}: {detail}")


@dataclass(frozen=True)
class Score:
    file: str
    function: str
    score: float
    complexity: int
    coverage: float


def crap(complexity: int, coverage: float) -> float:
    return round(complexity**2 * (1 - coverage) ** 3 + complexity, 2)


def decisions(node: ast.AST) -> int:
    match node:
        case (
            ast.If()
            | ast.For()
            | ast.AsyncFor()
            | ast.While()
            | ast.ExceptHandler()
            | ast.match_case()
            | ast.IfExp()
        ):
            return 1
        case ast.BoolOp(values=values):
            return len(values) - 1
        case ast.comprehension(ifs=ifs):
            return len(ifs)
        case _:
            return 0


def own_nodes(function: Definition) -> Iterator[ast.AST]:
    stack: list[ast.AST] = list(function.body)
    while stack:
        node = stack.pop()
        if isinstance(node, SCOPES):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def complexity(function: Definition) -> int:
    return 1 + sum(decisions(node) for node in own_nodes(function))


def as_object(value: object, path: str, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise InputError(path, f"{where} is not a JSON object")
    return cast(dict[str, object], value)


def as_list(value: object, path: str, where: str) -> list[object]:
    if not isinstance(value, list):
        raise InputError(path, f"{where} is not a JSON array")
    return cast(list[object], value)


def as_count(value: object, path: str, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError(path, f"{where} is not a count")
    return value


def load_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InputError(path, f"cannot read JSON: {error}") from error


def parse_source(path: str) -> dict[str, list[Definition]]:
    """Return each def in path under the dotted name coverage.py gives its region."""
    try:
        tree = ast.parse(Path(path).read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError) as error:
        raise InputError(path, f"cannot parse Python source: {error}") from error
    named: dict[str, list[Definition]] = {}
    for name, node in definitions(tree, ""):
        named.setdefault(name, []).append(node)
    return named


def definitions(node: ast.AST, scope: str) -> Iterator[tuple[str, Definition]]:
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, SCOPES):
            yield from definitions(child, scope)
            continue
        name = scope + child.name
        if not isinstance(child, ast.ClassDef):
            yield name, child
        yield from definitions(child, name + ".")


def body_lines(function: Definition) -> set[int]:
    last = function.body[-1]
    return set(range(function.body[0].lineno, (last.end_lineno or last.lineno) + 1))


def own_lines(function: Definition) -> set[int]:
    """Return the lines coverage.py counts in function: its body less nested defs."""
    nested = (
        node
        for node in ast.walk(function)
        if node is not function
        and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    return body_lines(function).difference(*map(body_lines, nested))


def branch_sources(value: object, path: str, where: str) -> list[int]:
    """Return the line each branch of a coverage.py branch list leaves from."""
    sources: list[int] = []
    for arc in as_list(value, path, where):
        pair = as_list(arc, path, where)
        if len(pair) != 2:
            raise InputError(path, f"{where} holds a branch that is not a pair")
        sources.append(as_count(pair[0], path, where))
    return sources


def lines_coverage(
    entry: dict[str, object], lines: set[int], path: str, file: str
) -> float:
    """Return the share of lines' statements and branches that the file's data ran."""
    executed, missing = (
        {
            as_count(line, path, f"{file} {name}")
            for line in as_list(entry.get(name), path, f"{file} {name}")
        }
        for name in ("executed_lines", "missing_lines")
    )
    hit, missed = (
        branch_sources(entry.get(name), path, f"{file} {name}")
        for name in ("executed_branches", "missing_branches")
    )
    return share(
        len(executed & lines),
        len((executed | missing) & lines),
        sum(line in lines for line in hit),
        sum(line in lines for line in hit + missed),
    )


def region_coverage(summary: dict[str, object], path: str, where: str) -> float:
    covered, statements, covered_branches, branches = (
        as_count(summary.get(name), path, f"{where} {name}")
        for name in (
            "covered_lines",
            "num_statements",
            "covered_branches",
            "num_branches",
        )
    )
    if covered > statements or covered_branches > branches:
        raise InputError(path, f"{where} covers more than it has")
    return share(covered, statements, covered_branches, branches)


def share(covered: int, statements: int, covered_branches: int, branches: int) -> float:
    """Return the share of statements and branches covered; 1.0 when there are none."""
    total = statements + branches
    return 1.0 if total == 0 else (covered + covered_branches) / total


def score_file(
    file: str, entry: dict[str, object], report_path: str
) -> Iterator[Score]:
    functions = as_object(entry.get("functions"), report_path, f"{file} functions")
    named = parse_source(str(Path.cwd() / file))
    for name, value in functions.items():
        if name == "":
            continue
        where = f"{file} function {name}"
        region = as_object(value, report_path, where)
        line = as_count(region.get("start_line"), report_path, f"{where} start_line")
        summary = as_object(region.get("summary"), report_path, f"{where} summary")
        found = named.pop(name, [])
        if line not in {node.lineno for node in found}:
            raise InputError(
                report_path, f"{where} starts on line {line}, not a def of that name"
            )
        yield from name_scores(file, name, found, entry, summary, report_path)
    if named:
        name, found = min(named.items(), key=lambda item: item[1][0].lineno)
        raise InputError(
            report_path, f"{file} line {found[0].lineno} defines {name} with no region"
        )


def name_scores(
    file: str,
    name: str,
    found: list[Definition],
    entry: dict[str, object],
    summary: dict[str, object],
    report_path: str,
) -> Iterator[Score]:
    """Score the defs that share name; later ones are named name#2, name#3.

    coverage.py reports one region per name, the last def's, so when defs
    share a name, such as a property's getter and setter, each is measured
    from the file's lines instead.
    """
    shares = (
        [region_coverage(summary, report_path, f"{file} function {name}")]
        if len(found) == 1
        else [
            lines_coverage(entry, own_lines(node), report_path, file) for node in found
        ]
    )
    for index, (node, share) in enumerate(zip(found, shares, strict=True), 1):
        count = complexity(node)
        key = name if index == 1 else f"{name}#{index}"
        yield Score(file, key, crap(count, share), count, share)


def read_report(path: str) -> list[Score]:
    report = as_object(load_json(path), path, "the report")
    meta = as_object(report.get("meta"), path, "meta")
    if meta.get("format") != 3:
        raise InputError(path, "is not a coverage.py JSON report of format 3")
    if meta.get("branch_coverage") is not True:
        raise InputError(path, "was measured without branch coverage")
    files = as_object(report.get("files"), path, "files")
    return [
        score
        for file, entry in files.items()
        for score in score_file(file, as_object(entry, path, file), path)
    ]


def table(scores: list[Score]) -> list[str]:
    rows = [("file", "function", "score", "complexity", "coverage")]
    rows += [
        (
            score.file,
            score.function,
            f"{score.score:.2f}",
            str(score.complexity),
            f"{score.coverage * 100:.1f}%",
        )
        for score in scores
    ]
    widths = [max(len(row[column]) for row in rows) for column in range(5)]
    return [
        "  ".join(
            cell.rjust(width) if column >= 2 else cell.ljust(width)
            for column, (cell, width) in enumerate(zip(row, widths, strict=True))
        )
        for row in rows
    ]


def arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", required=True, help="coverage.py JSON report")
    parser.add_argument("--limit", required=True, type=float, help="highest passing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = arguments(argv)
    limit = cast(float, options.limit)
    try:
        scores = read_report(cast(str, options.coverage))
    except InputError as error:
        print(f"crap: {error}", file=sys.stderr)
        return 1
    over = sorted(
        (score for score in scores if score.score > limit),
        key=lambda score: (score.file, score.function),
    )
    if over:
        print("\n".join(table(over)))
    print(f"{len(scores)} functions scored, {len(over)} over the limit of {limit:g}")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
