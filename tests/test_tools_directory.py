"""Load Built-in Tools from a directory the test builds.

Each definition is a copy of the bundled Ruff definition, so only its file name
and the other definitions beside it decide what loads.
"""

from importlib.resources import files

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.tools import load_directory

RUFF = files("gateseal").joinpath("data", "tools", "ruff.tool.toml").read_bytes()


def directory(tmp_path, *names):
    for name in names:
        (tmp_path / name).write_bytes(RUFF)
    return tmp_path


def test_only_a_tool_name_with_the_suffix_loads(tmp_path):
    found = load_directory(
        directory(tmp_path, "ruff.tool.toml", "black", "Black.tool.toml")
    )
    assert list(found) == ["ruff"]
    assert found["ruff"].name == "ruff"


def test_two_definitions_that_claim_one_hook_are_refused(tmp_path):
    with pytest.raises(GatesealError) as refused:
        load_directory(directory(tmp_path, "ruff.tool.toml", "ruff-copy.tool.toml"))
    assert refused.value.code == "duplicate_identifier"
    message = str(refused.value)
    assert all(value in message for value in ("ruff-copy", "'ruff-check'"))
    assert "ruff" in message.split()
