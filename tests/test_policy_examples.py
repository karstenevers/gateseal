"""The policy-format guide's alternatives fragment is part of add-a-tool's Black policy.

The add-a-tool guide's Gitleaks and Black policies have tests of their own,
which lock, compile and run them.
"""

import tomllib

from doc_examples import example


def within(part, whole):
    """Return whether every key of the TOML table part holds its value in whole."""
    if isinstance(part, dict):
        return isinstance(whole, dict) and all(
            key in whole and within(value, whole[key]) for key, value in part.items()
        )
    return part == whole


def test_the_alternatives_example_is_part_of_the_black_policy():
    alternatives = tomllib.loads(
        example("policy-format.md", "policy tables: alternatives")
    )
    black = tomllib.loads(example("add-a-tool.md", "policy tables: black"))
    assert alternatives["capabilities"]["python"]["alternatives"]
    assert within(alternatives, black)
