import pytest
from gateseal.diagnostics import GatesealError
from gateseal.validation import canonical, mapping, string


def failure(call, *arguments):
    with pytest.raises(GatesealError) as raised:
        call(*arguments)
    return raised.value.code, str(raised.value).split()


@pytest.mark.parametrize("value", [[], ["a"], {1: "a"}, {"a": 1, 2: "b"}, "a", None])
def test_an_object_has_only_string_keys(value):
    code, words = failure(mapping, value, "tools")
    assert code == "invalid_field"
    assert "tools" in words


def test_an_object_is_returned_as_it_is():
    value = {"ruff": {"pin": "0.17.1"}}
    assert mapping(value, "tools") is value


@pytest.mark.parametrize("value", ["", 7, None, "a\x01b", "\x1f", "tab\there"])
def test_a_string_is_nonempty_and_printable(value):
    code, words = failure(string, value, "release")
    assert code == "invalid_field"
    assert "release" in words


@pytest.mark.parametrize("value", ["a", "2026.11 team", "équipe"])
def test_a_printable_string_is_returned(value):
    assert string(value, "release") == value


def test_canonical_json_keeps_text_and_sorts_keys():
    assert canonical({"reason": "Prüfung", "a": [1]}) == (
        '{\n  "a": [\n    1\n  ],\n  "reason": "Prüfung"\n}\n'.encode()
    )


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_canonical_json_never_writes_a_number_json_lacks(number):
    with pytest.raises(ValueError):
        canonical({"size": number})
