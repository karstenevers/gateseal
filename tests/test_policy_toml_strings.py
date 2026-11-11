"""gateseal policy pin finds the pin past string lines that look like keys."""

from gateseal.policy_toml import pinned

POLICY = '''\
[components.ruff]
notes = """
reviewed by = the formatter team
"""
pin = "0.17.1"

[components.ruff.versions."0.17.1"]

[components.ruff.versions."0.18.0"]
'''


def test_pin_reads_past_a_string_line_whose_key_is_not_toml():
    result = pinned(POLICY.encode(), "ruff", "0.18.0").decode()
    assert result == POLICY.replace('pin = "0.17.1"', 'pin = "0.18.0"')
