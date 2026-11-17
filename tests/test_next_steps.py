"""Every failure names a next step, its own or the default of its error code.

The walk reads the package's source: each call that passes a literal error code
to a callable with a code parameter is one way to fail.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

import gateseal
from gateseal.diagnostics import NEXT_STEPS, GatesealError


def parameters(function: ast.FunctionDef) -> list[str]:
    arguments = function.args
    names = arguments.posonlyargs + arguments.args + arguments.kwonlyargs
    return [a.arg for a in names if a.arg != "self"]


def signatures(trees: list[ast.Module]) -> dict[str, list[str]]:
    """Return the parameters of each callable that takes a code, by its name."""
    found: dict[str, list[str]] = {}
    for node in (n for tree in trees for n in ast.walk(tree)):
        if isinstance(node, ast.FunctionDef):
            found[node.name] = parameters(node)
        elif isinstance(node, ast.ClassDef):
            for init in node.body:
                if isinstance(init, ast.FunctionDef) and init.name == "__init__":
                    found[node.name] = parameters(init)
    return {name: names for name, names in found.items() if "code" in names}


def calls(node: ast.AST, around: list[str]) -> Iterator[tuple[ast.Call, list[str]]]:
    """Yield each call under node with the parameters of the function around it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef):
            yield from calls(child, parameters(child))
            continue
        if isinstance(child, ast.Call):
            yield child, around
        yield from calls(child, around)


def literal_codes(node: ast.expr | None) -> list[str]:
    if isinstance(node, ast.IfExp):
        return literal_codes(node.body) + literal_codes(node.orelse)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def stepless_codes() -> set[str]:
    package = Path(gateseal.__file__).parent
    trees = [ast.parse(path.read_text()) for path in package.rglob("*.py")]
    callables = signatures(trees)
    codes: set[str] = set()
    for tree in trees:
        for call, around in calls(tree, []):
            func = call.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name not in callables:
                continue
            names = callables[name]
            bound = dict(zip(names, call.args, strict=False))
            bound |= {k.arg: k.value for k in call.keywords if k.arg}
            step = bound.get("next_step")
            if "next_step" in names and (
                step is None
                or (isinstance(step, ast.Constant) and step.value == "")
                or (isinstance(step, ast.Name) and step.id in around)
            ):
                codes.update(literal_codes(bound.get("code")))
    return codes


def test_every_failure_without_its_own_step_gets_a_default():
    assert stepless_codes() == set(NEXT_STEPS)
    for code in NEXT_STEPS:
        assert GatesealError(code, "message").next_step, code
