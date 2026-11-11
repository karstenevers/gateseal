"""gateseal policy add and pin edit policy.toml byte for byte.

Each test calls the edit on a hand-written policy.toml and compares the whole
result, because an edit that parses right can still put a table in the wrong
place, drop a blank line or change the line endings.
"""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy_toml import component, pinned, reviewed

NEWEST_FIRST = """\
schema = 4

# Reviewed formatter.
[components.ruff]
pin = "0.17.1"

[components.ruff.versions."0.17.1"]

[components.ruff.versions."0.15.0"]

[components.betterleaks]
pin = "1.9.0"

[components.betterleaks.versions."1.9.0"]
"""

OLDEST_FIRST = """\
[components.ruff]
pin = "0.17.1"

[components.ruff.versions."0.15.0"]

[components.ruff.versions."0.17.1"]
"""


def header(version):
    return f'[components.ruff.versions."{version}"]\n'


def test_a_version_goes_between_its_neighbours_newest_first():
    result = reviewed(NEWEST_FIRST.encode(), "ruff", "0.16.0").decode()
    assert result == NEWEST_FIRST.replace(
        header("0.15.0"), header("0.16.0") + "\n" + header("0.15.0")
    )


def test_the_newest_version_goes_first_when_the_newest_is_first():
    result = reviewed(NEWEST_FIRST.encode(), "ruff", "0.18.0").decode()
    assert result == NEWEST_FIRST.replace(
        header("0.17.1"), header("0.18.0") + "\n" + header("0.17.1")
    )


def test_the_oldest_version_goes_last_when_the_newest_is_first():
    result = reviewed(NEWEST_FIRST.encode(), "ruff", "0.14.0").decode()
    assert result == NEWEST_FIRST.replace(
        header("0.15.0"), header("0.15.0") + "\n" + header("0.14.0")
    )


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        (
            "0.16.0",
            OLDEST_FIRST.replace(
                header("0.17.1"), header("0.16.0") + "\n" + header("0.17.1")
            ),
        ),
        (
            "0.14.0",
            OLDEST_FIRST.replace(
                header("0.15.0"), header("0.14.0") + "\n" + header("0.15.0")
            ),
        ),
        ("0.18.0", OLDEST_FIRST + "\n" + header("0.18.0")),
    ],
)
def test_versions_written_oldest_first_stay_oldest_first(version, expected):
    assert reviewed(OLDEST_FIRST.encode(), "ruff", version).decode() == expected


def test_a_version_with_its_comment_and_subtables_is_one_block():
    block = (
        "# Our fixtures need it.\n" + header("0.15.0") + 'note = "x"\n\n'
        '[components.ruff.versions."0.15.0".platforms.linux]\narch = "x86_64"\n'
    )
    policy = NEWEST_FIRST.replace(header("0.15.0"), block)
    newer = reviewed(policy.encode(), "ruff", "0.16.0").decode()
    assert newer == policy.replace(block, header("0.16.0") + "\n" + block)
    older = reviewed(policy.encode(), "ruff", "0.14.0").decode()
    assert older == policy.replace(block, block + "\n" + header("0.14.0"))


def test_a_comment_right_above_a_header_stays_with_its_table():
    policy = NEWEST_FIRST.replace(
        header("0.17.1") + "\n",
        header("0.17.1") + 'note = "x"\n# Our fixtures need it.\n',
    )
    result = reviewed(policy.encode(), "ruff", "0.16.0").decode()
    assert result == policy.replace(
        'note = "x"\n', 'note = "x"\n\n' + header("0.16.0") + "\n"
    )


def test_a_policy_without_version_tables_gets_one_after_the_component():
    policy = """\
[components.ruff]
pin = "0.17.1"

[components.ruff.versions]
"0.17.1" = {}

[choices.python]
formatter = "ruff"
"""
    result = reviewed(policy.encode(), "ruff", "0.18.0").decode()
    assert result == policy.replace(
        'pin = "0.17.1"\n', 'pin = "0.17.1"\n\n' + header("0.18.0")
    )


def test_crlf_and_a_missing_final_newline_stay():
    policy = OLDEST_FIRST.replace("\n", "\r\n").removesuffix("\r\n")
    result = reviewed(policy.encode(), "ruff", "0.18.0").decode()
    assert result == policy + "\r\n\r\n" + header("0.18.0").replace("\n", "\r\n")


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("0.18.0", header("0.18.0") + "\n" + header("0.17.1").rstrip("\n")),
        ("0.16.0", header("0.17.1") + "\n" + header("0.16.0")),
    ],
)
def test_a_one_line_policy_without_a_final_newline(version, expected):
    policy = header("0.17.1").rstrip("\n")
    assert reviewed(policy.encode(), "ruff", version).decode() == expected


def test_reviewing_a_reviewed_version_is_refused():
    with pytest.raises(GatesealError) as raised:
        reviewed(NEWEST_FIRST.encode(), "ruff", "0.15.0")
    assert raised.value.code == "policy_edit_unsupported"
    assert '[components.ruff.versions."0.15.0"]' in raised.value.next_step
    assert "gateseal policy lock" in raised.value.next_step


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


def test_pin_finds_a_dotted_key_under_the_components_table():
    policy = '[components]\nruff.pin = "0.17.1"\nruff.versions."0.17.1" = {}\n'
    assert pinned(policy.encode(), "ruff", "0.18.0").decode() == policy.replace(
        '"0.17.1"\nruff', '"0.18.0"\nruff'
    )


ADDED = """\
[components.black]
pin = "26.10.1"

[components.black.versions."26.10.1"]
"""


def test_a_component_goes_after_the_last_components_table():
    policy = """\
[components.ruff]
pin = "0.17.1"

[components.ruff.versions."0.17.1"]


[choices.python]
formatter = "ruff"
"""
    result = component(policy.encode(), "black", "26.10.1").decode()
    assert result == policy.replace('"0.17.1"]\n\n', '"0.17.1"]\n\n' + ADDED + "\n", 1)


def test_an_array_of_tables_ends_the_components_tables():
    policy = """\
[components.ruff]
pin = "0.17.1"

[components.ruff.versions."0.17.1"]

[[notes]]
text = "reviewed"
"""
    result = component(policy.encode(), "black", "26.10.1").decode()
    assert result == policy.replace("[[notes]]", ADDED + "\n[[notes]]")


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("", ADDED),
        ("schema = 4\n", "schema = 4\n\n" + ADDED),
        (
            'schema = 4\r\n\r\n[components.ruff]\r\npin = "0.17.1"',
            'schema = 4\r\n\r\n[components.ruff]\r\npin = "0.17.1"\r\n\r\n'
            + ADDED.replace("\n", "\r\n"),
        ),
    ],
)
def test_a_component_keeps_the_files_line_endings(policy, expected):
    assert component(policy.encode(), "black", "26.10.1").decode() == expected
