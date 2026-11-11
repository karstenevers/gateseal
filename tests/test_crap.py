import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

CRAP = Path(__file__).resolve().parents[1] / "ci" / "crap.py"
spec = importlib.util.spec_from_file_location("crap", CRAP)
assert spec and spec.loader
crap = importlib.util.module_from_spec(spec)
sys.modules["crap"] = crap
spec.loader.exec_module(crap)

SCOPE = re.compile(r"(\s*)(?:async\s+)?(def|class)\s+(\w+)")
FULL = (1, 1, 0, 0)


def region(line, covered, statements, covered_branches, branches):
    return {
        "start_line": line,
        "summary": {
            "covered_lines": covered,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        },
    }


def project(root, sources, summaries=None):
    """Write sources and a coverage report with one region per def name.

    A region is named after its def and the defs and classes around it, as
    coverage.py names it, and is fully covered unless summaries gives
    (covered_lines, num_statements, covered_branches, num_branches). A
    repeated name keeps its last def's region, as in coverage.py.
    """
    summaries = summaries or {}
    files = {}
    for name, source in sources.items():
        (root / name).write_text(source)
        functions = {"": region(1, 0, 0, 0, 0)}
        scopes = []
        for number, line in enumerate(source.splitlines(), 1):
            if not (found := SCOPE.match(line)):
                continue
            indent, kind, own = found.groups()
            scopes = [scope for scope in scopes if scope[0] < len(indent)]
            scopes.append((len(indent), own))
            if kind == "def":
                qualified = ".".join(scope[1] for scope in scopes)
                summary = summaries.get(qualified, FULL)
                functions[qualified] = region(number, *summary)
        files[name] = {"functions": functions}
    report = root / "coverage.json"
    meta = {"format": 3, "version": "7.17.2", "branch_coverage": True}
    report.write_text(json.dumps({"meta": meta, "files": files}))
    return report


def branchy(name, complexity):
    tests = "".join(f"    if x == {n}:\n        pass\n" for n in range(complexity - 1))
    return f"def {name}(x):\n{tests}    return x\n"


def arguments(report, limit="10"):
    return ["--coverage", str(report), "--limit", limit]


def check(capsys, report, limit="10"):
    code = crap.main(arguments(report, limit))
    out = capsys.readouterr().out.splitlines()
    rows = [tuple(line.split()) for line in out[1:-1]]
    return code, rows, out[-1]


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_function_at_the_limit_passes(root, capsys):
    report = project(root, {"m.py": branchy("at", 10)})
    code, rows, summary = check(capsys, report)
    assert (code, rows) == (0, [])
    assert summary == "1 functions scored, 0 over the limit of 10"


def test_function_just_over_the_limit_fails(root, capsys):
    report = project(root, {"m.py": branchy("at", 10) + branchy("over", 11)})
    code, rows, summary = check(capsys, report)
    assert code == 1
    assert rows == [("m.py", "over", "11.00", "11", "100.0%")]
    assert summary == "2 functions scored, 1 over the limit of 10"


def test_coverage_counts_statements_and_branches(root, capsys):
    report = project(root, {"m.py": branchy("f", 5)}, {"f": (1, 2, 0, 2)})
    code, rows, _ = check(capsys, report)
    assert code == 1
    assert rows == [("m.py", "f", "15.55", "5", "25.0%")]


def test_region_without_statements_counts_as_covered(root, capsys):
    report = project(root, {"m.py": branchy("f", 11)}, {"f": (0, 0, 0, 0)})
    _, rows, _ = check(capsys, report)
    assert [row[2:] for row in rows] == [("11.00", "11", "100.0%")]


def edit_json(path, change):
    document = json.loads(path.read_text())
    change(document)
    path.write_text(json.dumps(document))


def report_edit(change):
    return lambda root: edit_json(root / "coverage.json", change)


def region_of_f(report):
    return report["files"]["m.py"]["functions"]["f"]


def with_file_lines(branches):
    def change(report):
        report["files"]["m.py"].update(
            executed_lines=[],
            missing_lines=[],
            executed_branches=branches,
            missing_branches=[],
        )

    return change


BAD_INPUT = {
    "missing report": (lambda root: (root / "coverage.json").unlink(), "coverage.json"),
    "report not JSON": (
        lambda root: (root / "coverage.json").write_text("{"),
        "coverage.json",
    ),
    "not format 3": (
        report_edit(lambda report: report["meta"].update(format=2)),
        "coverage.json",
    ),
    "branch coverage off": (
        report_edit(lambda report: report["meta"].update(branch_coverage=False)),
        "coverage.json",
    ),
    "region without start line": (
        report_edit(lambda report: region_of_f(report).pop("start_line")),
        "coverage.json",
    ),
    "region without def": (
        report_edit(lambda report: region_of_f(report).update(start_line=2)),
        "coverage.json",
    ),
    "covers more than it has": (
        report_edit(
            lambda report: region_of_f(report)["summary"].update(covered_lines=2)
        ),
        "coverage.json",
    ),
    "def without region": (
        lambda root: (root / "m.py").write_text(branchy("f", 1) + branchy("g", 1)),
        "coverage.json",
    ),
    "repeated name without file lines": (
        lambda root: (root / "m.py").write_text(branchy("f", 1) * 2),
        "coverage.json",
    ),
    "branch not a pair": (
        lambda root: (
            (root / "m.py").write_text(branchy("f", 1) * 2),
            edit_json(root / "coverage.json", with_file_lines(branches=[[1]])),
        ),
        "coverage.json",
    ),
    "source not Python": (lambda root: (root / "m.py").write_text("def f(:\n"), "m.py"),
    "source missing": (lambda root: (root / "m.py").unlink(), "m.py"),
}


@pytest.mark.parametrize(
    ("breakage", "named"), BAD_INPUT.values(), ids=BAD_INPUT.keys()
)
def test_bad_input_fails_naming_the_file(root, capsys, breakage, named):
    report = project(root, {"m.py": branchy("f", 1)})
    breakage(root)
    code = crap.main(arguments(report))
    error = capsys.readouterr().err
    assert code == 1
    assert error.count("\n") == 1 and named in error


COMPLEXITY = {
    "if and elif": (
        """
        def f(x):
            if x:
                pass
            elif not x:
                pass
        """,
        {"f": 3},
    ),
    "for and while": (
        """
        def f(x):
            for _ in x:
                while x:
                    break
        """,
        {"f": 3},
    ),
    "async for": (
        """
        async def f(x):
            async for _ in x:
                pass
        """,
        {"f": 2},
    ),
    "each except": (
        """
        def f():
            try:
                pass
            except ValueError:
                pass
            except KeyError:
                pass
            finally:
                pass
        """,
        {"f": 3},
    ),
    "except star": (
        """
        def f():
            try:
                pass
            except* ValueError:
                pass
        """,
        {"f": 2},
    ),
    "each match case": (
        """
        def f(x):
            match x:
                case 1:
                    pass
                case 2:
                    pass
                case _:
                    pass
        """,
        {"f": 4},
    ),
    "and and or": ("def f(a, b, c):\n    return a and b and c or a\n", {"f": 4}),
    "conditional expression": ("def f(x):\n    return 1 if x else 2\n", {"f": 2}),
    "comprehension filters, not its for": (
        "def f(x):\n    return [y for y in x for z in y if y if z]\n",
        {"f": 3},
    ),
    "lambda counts in its function": (
        "def f():\n    return lambda x: x or 1\n",
        {"f": 2},
    ),
    "nested def and class count on their own": (
        """
        def f(x):
            def g():
                if x:
                    pass

            class C:
                if x:
                    pass

                def h(self):
                    return x and x

            return g, C
        """,
        {"f": 1, "f.g": 2, "f.C.h": 2},
    ),
}


@pytest.mark.parametrize(
    ("source", "expected"), COMPLEXITY.values(), ids=COMPLEXITY.keys()
)
def test_complexity(root, source, expected):
    report = project(root, {"m.py": textwrap.dedent(source).lstrip()})
    measured = {score.function: score.complexity for score in crap.read_report(report)}
    assert measured == expected


FIXTURE = {
    "pkg/used.py": """
        import functools


        def logged(function):
            @functools.wraps(function)
            def wrapper(*args):
                return function(*args)

            return wrapper


        class Shape:
            def __init__(self, kind):
                self.kind = kind

            @logged
            def sides(self):
                match self.kind:
                    case "triangle":
                        return 3
                    case "square":
                        return 4
                    case _:
                        return 0


        def outer(values):
            def keep(value):
                return value > 0 and value < 10

            return [value for value in values if keep(value)]


        def idle(flag, other):
            if flag or other:
                return 1
            return 0


        class Box:
            @property
            def size(self):
                if self._size:
                    return self._size
                return 0

            @size.setter
            def size(self, value):
                self._size = value
        """,
    "pkg/unused.py": """
        def lonely(x):
            return x if x else None


        class Ghost:
            def haunt(self, items):
                for item in items:
                    while item:
                        item -= 1
        """,
    "drive.py": """
        from pkg.used import Box, Shape, outer

        for kind in ("triangle", "square", "circle"):
            Shape(kind).sides()
        outer([-1, 5, 20])
        box = Box()
        box.size = 3
        assert box.size == 3
        """,
}


@pytest.fixture(scope="module")
def measured(tmp_path_factory):
    root = tmp_path_factory.mktemp("measured")
    for name, source in FIXTURE.items():
        (root / name).parent.mkdir(exist_ok=True)
        (root / name).write_text(textwrap.dedent(source).lstrip())
    env = {k: v for k, v in os.environ.items() if not k.startswith("COVERAGE_")}
    for command in (
        ["run", "--branch", "--source", "pkg", "drive.py"],
        ["json", "-o", "coverage.json"],
    ):
        subprocess.run(
            [sys.executable, "-m", "coverage", *command],
            cwd=root,
            env=env,
            check=True,
            capture_output=True,
        )
    return root


def test_real_report_scores_every_function(measured, monkeypatch):
    monkeypatch.chdir(measured)
    scored = {
        (score.file, score.function): (
            score.score,
            score.complexity,
            round(score.coverage, 4),
        )
        for score in crap.read_report("coverage.json")
    }
    assert scored == {
        ("pkg/used.py", "logged"): (1.0, 1, 1.0),
        ("pkg/used.py", "logged.wrapper"): (1.0, 1, 1.0),
        ("pkg/used.py", "Shape.__init__"): (1.0, 1, 1.0),
        ("pkg/used.py", "Shape.sides"): (4.0, 4, 1.0),
        ("pkg/used.py", "outer"): (2.0, 2, 1.0),
        ("pkg/used.py", "outer.keep"): (2.0, 2, 1.0),
        ("pkg/used.py", "idle"): (12.0, 3, 0.0),
        ("pkg/used.py", "Box.size"): (2.26, 2, 0.6),
        ("pkg/used.py", "Box.size#2"): (1.0, 1, 1.0),
        ("pkg/unused.py", "lonely"): (6.0, 2, 0.0),
        ("pkg/unused.py", "Ghost.haunt"): (12.0, 3, 0.0),
    }


def test_real_report_fails_on_untested_functions(measured, monkeypatch, capsys):
    monkeypatch.chdir(measured)
    code, rows, summary = check(capsys, measured / "coverage.json")
    assert code == 1
    assert rows == [
        ("pkg/unused.py", "Ghost.haunt", "12.00", "3", "0.0%"),
        ("pkg/used.py", "idle", "12.00", "3", "0.0%"),
    ]
    assert summary == "11 functions scored, 2 over the limit of 10"
