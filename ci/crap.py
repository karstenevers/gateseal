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

Definition = ast.FunctionDef | ast.AsyncFunctionDef

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


def as_count(value: object, path: str, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise InputError(path, f"{where} is not a count")
    return value


def load_json(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InputError(path, f"cannot read JSON: {error}") from error


def parse_source(path: str) -> dict[tuple[str, int], Definition]:
    """Return each def in path under its dotted name and the line of its def."""
    try:
        tree = ast.parse(Path(path).read_bytes(), filename=path)
    except (OSError, SyntaxError, ValueError) as error:
        raise InputError(path, f"cannot parse Python source: {error}") from error
    return {(name, node.lineno): node for name, node in definitions(tree, "")}


def definitions(node: ast.AST, scope: str) -> Iterator[tuple[str, Definition]]:
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, SCOPES):
            yield from definitions(child, scope)
            continue
        name = scope + child.name
        if not isinstance(child, ast.ClassDef):
            yield name, child
        yield from definitions(child, name + ".")


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
    """Score each def from its own region.

    coverage.py names a region after its def and the scopes around it. When
    defs share that name, such as a property's getter and setter, the later
    ones get "@" and their line, as in Box.size@48.
    """
    functions = as_object(entry.get("functions"), report_path, f"{file} functions")
    defs = parse_source(str(Path.cwd() / file))
    for name, value in functions.items():
        if name == "":
            continue
        where = f"{file} function {name}"
        region = as_object(value, report_path, where)
        line = as_count(region.get("start_line"), report_path, f"{where} start_line")
        summary = as_object(region.get("summary"), report_path, f"{where} summary")
        node = defs.pop((name.partition("@")[0], line), None)
        if node is None:
            raise InputError(
                report_path, f"{where} starts on line {line}, not a def of that name"
            )
        count = complexity(node)
        covered = region_coverage(summary, report_path, where)
        yield Score(file, name, crap(count, covered), count, covered)
    if defs:
        name, line = min(defs, key=lambda key: key[1])
        raise InputError(
            report_path, f"{file} line {line} defines {name} with no region"
        )


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
