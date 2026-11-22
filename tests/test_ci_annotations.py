"""The commands and the summary that a CI provider gets from a gate's Result.

Entries take the shape that the gate gives them: a hook or a history scan,
then its findings. Each failed one becomes an error annotation per finding, or
one annotation of the run without a finding.
"""

import pytest
from gateseal.ci_providers import PROVIDERS
from gateseal.diagnostics import Note, Result

COMMIT = "c" * 40
TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0" * 2


def hook(status, message):
    return {"category": "hook", "status": status, "message": message}


def finding(file, line, column, message, **extra):
    return {
        "category": "finding",
        "tool": "ruff",
        "rule": "F401",
        "file": file,
        "line": line,
        "column": column,
        "message": message,
    } | extra


def scan(status):
    return {
        "category": "history",
        "status": status,
        "message": f"betterleaks scanned the commits a..b: {status}.",
    }


def gate(*entries, notes=(), code=1):
    duration = {"category": "duration", "message": "The gate took 1.0 seconds."}
    return Result(
        "gate",
        "checks_failed" if code else "checks_passed",
        "Done.",
        code,
        next_steps=("Fix it.",) if code else (),
        notes=notes,
        entries=(duration, *entries),
    )


ANNOTATION = {"github": ("::error", "::warning"), "azure": ("##vso[task.logissue",)}


def annotations(result, provider):
    shown = result.render(False, ci=PROVIDERS[provider])
    return [
        line for line in shown.splitlines() if line.startswith(ANNOTATION[provider])
    ]


FAILED_HOOK = hook("failed", "ruff check: Failed")
NOTEBOOK_FINDING = finding("a.ipynb", 4, 2, "`os` imported but unused", cell=3)


@pytest.mark.parametrize(
    "provider,expected",
    [
        (
            "github",
            [
                "::error file=a.py,line=1,col=8,title=gateseal::"
                "ruff F401: `os` imported but unused",
                "::error file=a.ipynb,title=gateseal::"
                "ruff F401: `os` imported but unused (cell 3, line 4)",
            ],
        ),
        (
            "azure",
            [
                "##vso[task.logissue type=error;sourcepath=a.py;linenumber=1;"
                "columnnumber=8]ruff F401: `os` imported but unused",
                "##vso[task.logissue type=error;sourcepath=a.ipynb]"
                "ruff F401: `os` imported but unused (cell 3, line 4)",
            ],
        ),
    ],
)
def test_a_finding_annotates_its_line_and_a_notebook_finding_its_file(
    provider, expected
):
    result = gate(
        FAILED_HOOK, finding("a.py", 1, 8, "`os` imported but unused"), NOTEBOOK_FINDING
    )
    assert annotations(result, provider) == expected


@pytest.mark.parametrize(
    "provider,expected",
    [
        (
            "github",
            [
                "::error title=gateseal::ruff check: Failed",
                "::error title=gateseal::betterleaks aws-access-token: (AWS) "
                f"at leak.txt:1 in commit {COMMIT}",
                "::error title=gateseal::betterleaks scanned the commits a..b: failed.",
            ],
        ),
        (
            "azure",
            [
                "##vso[task.logissue type=error]ruff check: Failed",
                "##vso[task.logissue type=error]betterleaks aws-access-token: "
                f"(AWS) at leak.txt:1 in commit {COMMIT}",
                "##vso[task.logissue type=error]"
                "betterleaks scanned the commits a..b: failed.",
            ],
        ),
    ],
)
def test_each_failed_hook_and_history_scan_is_annotated_and_history_on_the_run(
    provider, expected
):
    history_finding = finding(
        "leak.txt", 1, 7, "(AWS)", tool="betterleaks", rule="aws-access-token"
    )
    result = gate(
        FAILED_HOOK,
        hook("passed", "check yaml: Passed"),
        finding("passed.py", 2, 1, "not annotated"),
        scan("failed"),
        history_finding | {"commit": COMMIT},
        scan("failed"),
    )
    assert annotations(result, provider) == expected


def test_each_failed_hook_gets_its_first_annotation_before_any_second_one():
    result = gate(
        FAILED_HOOK,
        finding("a.py", 1, 1, "first"),
        finding("a.py", 2, 1, "second"),
        hook("failed", "mypy: Failed"),
        finding("b.py", 3, 1, "third", tool="mypy"),
    )
    shown = annotations(result, "github")
    assert [line.rpartition(": ")[2] for line in shown] == ["first", "third", "second"]


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("github", "::error title=gateseal::100%25 of a%0D%0Aline"),
        ("azure", "##vso[task.logissue type=error]100%AZP25 of a%0D%0Aline"),
    ],
)
def test_a_message_escapes_percent_and_line_breaks(provider, expected):
    result = gate(hook("failed", "100% of a\r\nline"))
    assert annotations(result, provider)[-1] == expected


@pytest.mark.parametrize(
    "provider,expected",
    [
        ("github", "::warning title=gateseal::Slow."),
        ("azure", "##vso[task.logissue type=warning]Slow."),
    ],
)
def test_a_passing_gate_keeps_its_warning_and_has_no_error(provider, expected):
    result = gate(
        hook("passed", "check yaml: Passed"),
        notes=(Note("warning", "over_time_budget", "Slow."),),
        code=0,
    )
    assert annotations(result, provider) == [expected]


@pytest.mark.parametrize(
    "provider,file",
    [("github", "file=[redacted]/a.py,"), ("azure", "sourcepath=[redacted%5D/a.py;")],
)
def test_an_annotation_holds_no_secret(provider, file):
    result = gate(
        hook("failed", f"hook {TOKEN}: Failed"),
        hook("failed", "betterleaks: Failed"),
        finding(f"{TOKEN}/a.py", 1, 2, f"token {TOKEN}"),
    )
    shown = annotations(result, provider)
    assert len(shown) == 2
    assert not [line for line in shown if TOKEN[4:] in line]
    assert file in shown[1]


def test_the_summary_lists_each_failed_hook_and_history_scan_and_the_next_steps():
    result = gate(
        FAILED_HOOK,
        hook("passed", "check yaml: Passed"),
        finding("a.py", 1, 8, "unused"),
        scan("failed"),
    )
    assert result.summary() == (
        "### gateseal gate\n\nDone.\n\nFailed checks:\n\n- ruff check: Failed\n"
        "- betterleaks scanned the commits a..b: failed.\n\nNext steps:\n\n"
        "- Fix it.\n"
    )


def test_a_summary_leaves_out_empty_lists():
    assert Result("gate", "tool_failed", "Stopped.", 4).summary() == (
        "### gateseal gate\n\nStopped.\n"
    )


def test_the_summary_holds_no_secret():
    result = Result(
        "gate",
        "checks_failed",
        f"Failed {TOKEN} here.",
        1,
        next_steps=(f"Revoke {TOKEN} now.",),
        entries=(hook("failed", f"hook {TOKEN}: Failed"),),
    )
    assert result.summary() == (
        "### gateseal gate\n\nFailed [redacted] here.\n\nFailed checks:\n\n"
        "- hook [redacted]: Failed\n\nNext steps:\n\n- Revoke [redacted] now.\n"
    )
