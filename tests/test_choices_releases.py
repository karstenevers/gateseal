"""A Policy Choice value that the selected release of its tool must accept."""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.tools.choices import Choice

TARGET = Choice(
    "target-version", {"py39": "0.1.0", "py313": "0.4.5", "py315": "0.14.11"}
)
FIELD = "choices.python.target_version"


def test_a_value_is_accepted_from_the_release_that_added_it():
    TARGET.require("py313", "ruff", "0.4.5", FIELD)


@pytest.mark.parametrize(
    "release,accepted",
    [("0.4.5", ["py39", "py313"]), ("0.0.9", [])],
    ids=["some accepted", "none accepted"],
)
def test_a_refused_value_lists_what_the_release_accepts(release, accepted):
    with pytest.raises(GatesealError) as failure:
        TARGET.require("py315", "ruff", release, FIELD)
    assert failure.value.code == "invalid_choice"
    message = str(failure.value)
    assert FIELD in message
    assert f"ruff {release}" in message
    assert [value for value in ("py39", "py313") if value in message] == accepted
    assert f"ruff {release}" in failure.value.next_step
