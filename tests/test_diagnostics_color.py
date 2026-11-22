"""Redaction sees through terminal color codes and keeps the color of clean lines."""

import pytest
from gateseal.diagnostics import redact

HALF = "A1b2C3d4E5f6G7h8I9j0"
SPLIT = f"ghp_{HALF}\x1b[31m{HALF}\x1b[0m"
"""A token whose color code falls between its two halves."""
LEADING = f"\x1b[1mghp_{HALF}{HALF}\x1b[0m"
"""A token right after a color code, with no word boundary before it."""
COLON_SPLIT = f"ghp_{HALF}\x1b[38:5:196m{HALF}\x1b[0m"
COLON_LEADING = f"\x1b[38:2::255:0:0mghp_{HALF}{HALF}\x1b[0m"
"""The same, with color codes whose parameters colons separate."""


@pytest.mark.parametrize(
    "secret",
    [SPLIT, LEADING, COLON_SPLIT, COLON_LEADING],
    ids=["split", "leading", "colon split", "colon leading"],
)
def test_a_color_code_hides_no_part_of_a_token(secret):
    shown = redact(f"token {secret} end")
    assert shown == "token [redacted] end"
    assert HALF not in shown


def test_only_the_line_with_a_token_loses_its_color():
    clean = "check yaml....\x1b[42mPassed\x1b[m"
    shown = redact(f"{clean}\n\x1b[31mtoken {SPLIT}\x1b[m\n{clean}")
    assert shown == f"{clean}\ntoken [redacted]\n{clean}"
