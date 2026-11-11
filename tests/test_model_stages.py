"""The stages a parsed Tool Definition records."""

from importlib.resources import files

from gateseal.tools import load_definition
from gateseal.tools.model import Origin, Stage

LISTED = 'stages = ["commit", "run", "ci"]'


def test_a_definition_records_the_stages_it_lists_in_any_order():
    data = files("gateseal").joinpath("data", "tools", "ruff.tool.toml").read_text()
    assert LISTED in data
    reordered = data.replace(LISTED, 'stages = ["ci", "commit", "run"]')
    definition = load_definition("ruff", reordered.encode(), Origin.TEAM)
    assert definition.stages == {Stage.COMMIT, Stage.RUN, Stage.CI}
