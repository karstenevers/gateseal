"""The stages a parsed Tool Definition records."""

from dataclasses import replace
from importlib.resources import files

import pytest
from gateseal import __version__
from gateseal.diagnostics import GatesealError
from gateseal.tools import load_definition
from gateseal.tools.model import Origin, Scope, Stage

LISTED = 'stages = ["commit", "run", "ci"]'


def ruff(stages):
    data = files("gateseal").joinpath("data", "tools", "ruff.tool.toml").read_text()
    assert LISTED in data
    return load_definition(
        "ruff", data.replace(LISTED, f"stages = {stages}").encode(), Origin.TEAM
    )


def test_a_definition_records_the_stages_it_lists_in_any_order():
    assert ruff('["ci", "commit", "run"]').stages == {
        Stage.COMMIT,
        Stage.RUN,
        Stage.CI,
    }


@pytest.mark.parametrize("stages", ['["push", "ci"]', '["ci", "push"]'])
def test_a_definition_can_run_its_hooks_at_a_push_and_in_the_gate(stages):
    definition = ruff(stages)
    assert definition.stages == {Stage.PUSH, Stage.CI}
    hook = definition.hooks["ruff-check"]
    entry = hook.base_entry(None, definition.stages, definition.scope)
    assert entry["stages"] == ["pre-push", "manual"]
    assert "always_run" not in entry


def test_a_project_hook_without_file_types_starts_at_every_push():
    definition = ruff('["push", "ci"]')
    hook = replace(definition.hooks["ruff-check"], types_or=())
    assert hook.base_entry(None, definition.stages, Scope.PROJECT) == {
        "id": "ruff-check",
        "name": "ruff check",
        "stages": ["pre-push", "manual"],
        "always_run": True,
        "pass_filenames": False,
    }


def test_a_project_hook_with_file_types_starts_only_for_a_file_of_those_types():
    """always_run is false even so, since a hook repository may set it true."""
    definition = ruff('["push", "ci"]')
    hook = definition.hooks["ruff-check"]
    assert hook.base_entry(None, definition.stages, Scope.PROJECT) == {
        "id": "ruff-check",
        "name": "ruff check",
        "stages": ["pre-push", "manual"],
        "always_run": False,
        "pass_filenames": False,
        "types_or": ["python", "pyi", "jupyter"],
    }


@pytest.mark.parametrize(
    "stages",
    [
        '["push"]',
        '["commit", "push", "ci"]',
        '["run", "push", "ci"]',
        '["push", "push", "ci"]',
        '["ci", "ci"]',
        "[]",
    ],
)
def test_a_definition_lists_one_of_the_three_stage_sets(stages):
    """A hook outside the gate, or at a commit and at a push, is no stage set."""
    with pytest.raises(GatesealError) as failure:
        ruff(stages)
    assert failure.value.code == "unsupported_tool_definition"
    listed = stages.strip("[]").replace('"', "")
    assert str(failure.value) == (
        f"tools.ruff.stages is {listed!r}, which gateseal {__version__} does not "
        "implement."
    )
