"""Plan gateseal gate's scope from its revision flags, through main in-process.

No pre-commit is set up, so a planned scope reaches the start of pre-commit and
stops with needs_setup, while a refused or empty scope stops before it.
"""

import contextlib
import io
import json

import pytest
from conftest import named_flags
from gateseal.cli import main
from installation import clone, success

ZEROS = "0" * 40


def gate(root, *flags):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), "--json", "gate", *flags])
    result = json.loads(output.getvalue())
    assert result["command"] == "gate"
    return code, result["code"], result["diagnostics"][0]["message"]


def head(root):
    return success(["git", "rev-parse", "HEAD"], root).stdout.strip()


@pytest.fixture(scope="module")
def commits(tmp_path_factory):
    """A configured consumer with two commits: first and second."""
    root = tmp_path_factory.mktemp("cli gate")
    consumer = clone(root / "origin", root / "consumer")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        configured = main(
            ["--repo", str(consumer), "configure", "--capability", "hygiene"]
        )
    assert configured == 0, output.getvalue()
    success(["git", "add", "-A"], consumer)
    success(["git", "commit", "--quiet", "-m", "Adopt hygiene"], consumer)
    first = head(consumer)
    (consumer / "notes.txt").write_text("notes\n")
    success(["git", "add", "notes.txt"], consumer)
    success(["git", "commit", "--quiet", "-m", "Notes"], consumer)
    return consumer, first, head(consumer)


@pytest.mark.parametrize(
    "flags",
    [
        [],
        ["--base", "{first}", "--head", "{second}"],
        ["--before", "{first}", "--after", "{second}"],
        ["--before", ZEROS, "--after", "{second}"],
        ["--after", "{second}"],
    ],
    ids=["every file", "change", "push", "new branch", "push without before"],
)
def test_a_planned_scope_reaches_pre_commit(commits, flags, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    root, first, second = commits
    filled = [f.format(first=first, second=second) for f in flags]
    code, found, _ = gate(root, *filled)
    assert (code, found) == (3, "needs_setup")


def test_a_push_that_deleted_its_ref_has_nothing_to_check(commits):
    root, first, _ = commits
    code, found, _ = gate(root, "--before", first, "--after", ZEROS)
    assert (code, found) == (0, "nothing_to_check")


@pytest.mark.parametrize(
    "flags,flag",
    [
        (["--base", "{unknown}", "--head", "{second}"], None),
        (["--before", "{first}", "--after", "{unknown}"], None),
        (["--base", "{first}", "--head", "HEAD"], "--head"),
        (["--before", "HEAD", "--after", "{second}"], "--before"),
    ],
    ids=["unknown base", "unknown after", "named head", "named before"],
)
def test_each_revision_is_checked_against_the_clone(commits, flags, flag):
    root, first, second = commits
    unknown = "1" * 40
    filled = [f.format(first=first, second=second, unknown=unknown) for f in flags]
    code, found, text = gate(root, *filled)
    if flag is None:
        assert (code, found) == (2, "unknown_revision")
        assert unknown in text
    else:
        assert (code, found) == (2, "invalid_invocation")
        assert named_flags(text) == {flag}


@pytest.mark.parametrize(
    "flags",
    [
        ["--base", "{first}"],
        ["--head", "{second}"],
        ["--before", "{first}"],
        ["--base", "{first}", "--after", "{second}"],
        ["--base", "{first}", "--head", "{second}", "--before", "{first}"]
        + ["--after", "{second}"],
        ["--merge", "{second}", "--after", "{second}"],
        ["--merge", "{second}", "--base", "{first}", "--head", "{second}"],
        ["--merge", "{second}", "--head", "{second}"],
    ],
    ids=[
        "base",
        "head",
        "before",
        "base and after",
        "both pairs",
        "merge and after",
        "merge and change",
        "merge and head",
    ],
)
def test_revision_flags_must_form_one_complete_group(commits, flags):
    root, first, second = commits
    filled = [f.format(first=first, second=second) for f in flags]
    code, found, text = gate(root, *filled)
    assert (code, found) == (2, "invalid_invocation")
    assert named_flags(text) == {"--base", "--head", "--merge", "--before", "--after"}


def test_a_merge_flag_plans_from_the_commit_it_names(commits):
    root, _, second = commits
    code, found, text = gate(root, "--merge", second)
    assert (code, found) == (2, "invalid_invocation")
    assert second in text and "fewer than two parents" in text
    code, found, text = gate(root, "--merge", "HEAD")
    assert (code, found) == (2, "invalid_invocation")
    assert named_flags(text) == {"--merge"}
