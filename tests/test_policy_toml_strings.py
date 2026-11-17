"""gateseal policy add and pin find their key past lines that only look like keys."""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy_toml import pinned, reviewed

POLICY = '''\
[capabilities.python.tools.ruff]
notes = """
reviewed by = the formatter team
"""
pin = "0.17.1"
reviewed = ["0.17.1", "0.18.0"]
'''


def test_pin_reads_past_a_string_line_whose_key_is_not_toml():
    result = pinned(POLICY.encode(), "ruff", "0.18.0").decode()
    assert result == POLICY.replace('pin = "0.17.1"', 'pin = "0.18.0"')


def test_add_reads_past_a_string_line_whose_key_is_not_toml():
    result = reviewed(POLICY.encode(), "ruff", "0.19.0").decode()
    assert result == POLICY.replace('"0.18.0"]', '"0.18.0", "0.19.0"]')


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("reviewed by = the formatter team", 'reviewed = "by the formatter team"'),
        ('"0.18.0"]', '"\\u0030.18.0"]'),
    ],
    ids=["string line that is a key", "escaped version"],
)
def test_add_refuses_a_list_it_cannot_read(before, after):
    policy = POLICY.replace(before, after)
    with pytest.raises(GatesealError) as raised:
        reviewed(policy.encode(), "ruff", "0.19.0")
    assert raised.value.code == "policy_edit_unsupported"
