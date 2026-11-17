"""How a Result reads in the terminal and as JSON."""

import json

import pytest
from gateseal.diagnostics import Note, Result, redact


def test_a_url_keeps_its_path_and_loses_only_a_query():
    assert redact("see https://host/team/x.git and https://host/y?token=1 now") == (
        "see https://host/team/x.git and https://host/y?[redacted] now"
    )


@pytest.mark.parametrize("url", ["https://host:99999/x", "https://[::1/x"])
def test_a_url_that_cannot_be_parsed_is_redacted_whole(url):
    assert redact(f"before {url} after") == "before [redacted-url] after"


@pytest.mark.parametrize(
    "size,cut", [(6000, False), (6001, True)], ids=["at the limit", "past the limit"]
)
def test_details_past_6000_characters_are_cut(size, cut):
    result = Result("run", details="x" * size)
    shown, newline, notice = result.render(False).partition("\n")
    assert shown == "x" * min(size, 6000)
    assert (bool(newline), "--verbose" in notice) == (cut, cut)
    assert result.render(False, full=True) == "x" * size


@pytest.mark.parametrize(
    "result,status",
    [
        (Result("check"), "ok"),
        (Result("configure", changed=("gateseal.lock",)), "changed"),
        (
            Result("configure", "refused", "No.", 2, changed=("gateseal.lock",)),
            "failed",
        ),
    ],
)
def test_the_json_status_says_whether_it_failed_or_changed_files(result, status):
    assert json.loads(result.render(True))["status"] == status


def test_json_keeps_non_ascii_text_readable():
    rendered = Result("check", message="Policy für Teams").render(True)
    assert "Policy für Teams" in rendered


def test_a_cut_entry_list_counts_what_it_left_out():
    entries = tuple({"category": "finding", "file": f"f{n}.py"} for n in range(203))
    *shown, cut = json.loads(Result("run", entries=entries).render(True))["diagnostics"]
    assert len(shown) == 200
    assert cut["category"] == "truncated"
    assert cut["message"].split()[0] == "3"
    assert "--verbose" in cut["message"]


def test_a_workflow_command_escapes_each_warning_and_json_ignores_the_flag():
    result = Result(
        "check",
        message="Consistent.",
        notes=(
            Note("info", "policy", "Policy 1."),
            Note("warning", "overdue", "100%\r\nreview"),
        ),
        next_steps=("Look.",),
    )
    assert result.render(False, workflow_commands=True).splitlines() == [
        "Consistent.",
        "Policy 1.",
        "::warning title=gateseal::100%25%0D%0Areview",
        "Next: Look.",
    ]
    assert "Warning: 100%\r\nreview" in result.render(False)
    assert result.render(True, workflow_commands=True) == result.render(True)


def test_entries_stay_out_of_text_output_whatever_their_severity():
    entry = {"severity": "warning", "category": "exception", "message": "Redundant."}
    result = Result("upgrade", message="Upgraded.", entries=(entry,))
    assert result.render(False) == "Upgraded."
    assert entry in json.loads(result.render(True))["diagnostics"]
