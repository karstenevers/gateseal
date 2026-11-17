"""gateseal policy add and pin edit policy.toml byte for byte.

Each test calls the edit on a hand-written policy.toml and compares the whole
result, because an edit that parses right can still put a version in the
wrong place, drop a comment or change the line endings.
"""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy_toml import pinned, reviewed

NEWEST_FIRST = """\
schema = 5

# Reviewed formatter.
[capabilities.python.tools.ruff]
pin = "0.17.1"
reviewed = ["0.17.1", "0.15.0"]

[capabilities.secrets.tools.betterleaks]
pin = "1.9.0"
reviewed = ["1.9.0"]
"""

OLDEST_FIRST = """\
[capabilities.python.tools.ruff]
pin = "0.17.1"
reviewed = ["0.15.0", "0.17.1"]
"""

ONE_PER_LINE = """\
[capabilities.python.tools.ruff]
pin = "0.17.1"
reviewed = [
    "0.17.1",  # current
    "0.15.0",
]
"""


def listed(*versions):
    return "reviewed = [" + ", ".join(f'"{version}"' for version in versions) + "]"


@pytest.mark.parametrize(
    ("version", "versions"),
    [
        ("0.16.0", ("0.17.1", "0.16.0", "0.15.0")),
        ("0.18.0", ("0.18.0", "0.17.1", "0.15.0")),
        ("0.14.0", ("0.17.1", "0.15.0", "0.14.0")),
    ],
)
def test_versions_written_newest_first_stay_newest_first(version, versions):
    result = reviewed(NEWEST_FIRST.encode(), "ruff", version).decode()
    assert result == NEWEST_FIRST.replace(listed("0.17.1", "0.15.0"), listed(*versions))


@pytest.mark.parametrize(
    ("version", "versions"),
    [
        ("0.16.0", ("0.15.0", "0.16.0", "0.17.1")),
        ("0.14.0", ("0.14.0", "0.15.0", "0.17.1")),
        ("0.18.0", ("0.15.0", "0.17.1", "0.18.0")),
    ],
)
def test_versions_written_oldest_first_stay_oldest_first(version, versions):
    result = reviewed(OLDEST_FIRST.encode(), "ruff", version).decode()
    assert result == OLDEST_FIRST.replace(listed("0.15.0", "0.17.1"), listed(*versions))


@pytest.mark.parametrize(
    ("version", "before", "after"),
    [
        ("0.18.0", '    "0.17.1",', '    "0.18.0",\n    "0.17.1",'),
        ("0.16.0", '    "0.15.0",', '    "0.16.0",\n    "0.15.0",'),
        ("0.14.0", '    "0.15.0",', '    "0.15.0",\n    "0.14.0",'),
    ],
)
def test_a_version_per_line_gets_a_line_of_its_own(version, before, after):
    result = reviewed(ONE_PER_LINE.encode(), "ruff", version).decode()
    assert result == ONE_PER_LINE.replace(before, after)


def test_the_last_version_without_a_comma_gets_one_on_its_line():
    policy = ONE_PER_LINE.replace('"0.15.0",\n]', '"0.15.0"\n]')
    result = reviewed(policy.encode(), "ruff", "0.14.0").decode()
    assert result == policy.replace('"0.15.0"\n]', '"0.15.0", "0.14.0"\n]')


def test_a_version_takes_its_neighbours_quote():
    policy = OLDEST_FIRST.replace(listed("0.15.0", "0.17.1"), "reviewed = ['0.17.1']")
    result = reviewed(policy.encode(), "ruff", "0.18.0").decode()
    assert result == policy.replace("['0.17.1']", "['0.18.0', '0.17.1']")


def test_crlf_and_a_missing_final_newline_stay():
    policy = ONE_PER_LINE.replace("\n", "\r\n").removesuffix("\r\n")
    result = reviewed(policy.encode(), "ruff", "0.18.0").decode()
    assert result == policy.replace('    "0.17.1"', '    "0.18.0",\r\n    "0.17.1"')


def test_a_tool_written_as_dotted_keys_is_edited_in_place():
    policy = (
        '[capabilities.python]\ntools.ruff.pin = "0.17.1"\n'
        'tools.ruff.reviewed = ["0.17.1"]\n'
    )
    added = reviewed(policy.encode(), "ruff", "0.18.0").decode()
    assert added == policy.replace('["0.17.1"]', '["0.18.0", "0.17.1"]')
    assert pinned(added.encode(), "ruff", "0.18.0").decode() == added.replace(
        'pin = "0.17.1"', 'pin = "0.18.0"'
    )


def test_a_tool_written_as_an_inline_table_is_refused():
    policy = (
        "[capabilities.python]\n"
        'tools = { ruff = { pin = "0.17.1", reviewed = ["0.17.1"] } }\n'
    )
    with pytest.raises(GatesealError) as raised:
        reviewed(policy.encode(), "ruff", "0.18.0")
    assert raised.value.code == "policy_edit_unsupported"
    assert raised.value.next_step == (
        'Add "0.18.0" to reviewed under [capabilities.python.tools.ruff] in '
        "policy.toml yourself, then run gateseal policy lock."
    )
    with pytest.raises(GatesealError) as raised:
        pinned(policy.encode(), "ruff", "0.18.0")
    assert raised.value.next_step == (
        'Set pin = "0.18.0" under [capabilities.python.tools.ruff] in policy.toml '
        "yourself, then run gateseal policy check."
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ('pin = "0.17.1"\n', 'pin = "0.18.0"\n'),
        ("pin='0.17.1'  # reviewed\n", "pin='0.18.0'  # reviewed\n"),
        ('"pin"="0.17.1"\n', '"pin"="0.18.0"\n'),
    ],
)
def test_pin_rewrites_only_the_version_in_its_line(before, after):
    policy = NEWEST_FIRST.replace('pin = "0.17.1"\n', before)
    result = pinned(policy.encode(), "ruff", "0.18.0").decode()
    assert result == policy.replace(before, after)
