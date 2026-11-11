"""Plan gateseal gate's scope from its revision flags, through main in-process.

The worktree is configured but never prepared, so a planned scope reaches the
preparation check and stops with needs_activation, while a refused or empty
scope stops before it.
"""

import contextlib
import io
import json

import pytest
from conftest import named_flags
from gateseal.cli import main
from installation import clone, success
from test_cli_commands import SHA, SOURCE

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
    """A configured, unprepared consumer with two commits: first and second."""
    root = tmp_path_factory.mktemp("cli gate")
    consumer = clone(root / "origin", root / "consumer")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        configured = main(
            ["--repo", str(consumer), "configure", "--capability", "hygiene"]
            + ["--compiler-source", SOURCE, "--compiler-sha256", SHA]
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
    ],
    ids=["every file", "change", "push", "new branch"],
)
def test_a_planned_scope_reaches_the_preparation(commits, flags):
    root, first, second = commits
    filled = [f.format(first=first, second=second) for f in flags]
    code, found, _ = gate(root, *filled)
    assert (code, found) == (3, "needs_activation")


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
        ["--after", "{second}"],
        ["--base", "{first}", "--after", "{second}"],
        ["--base", "{first}", "--head", "{second}", "--before", "{first}"]
        + ["--after", "{second}"],
    ],
    ids=["base", "head", "before", "after", "base and after", "both pairs"],
)
def test_revision_flags_must_form_one_complete_pair(commits, flags):
    root, first, second = commits
    filled = [f.format(first=first, second=second) for f in flags]
    code, found, text = gate(root, *filled)
    assert (code, found) == (2, "invalid_invocation")
    assert named_flags(text) == {"--base", "--head", "--before", "--after"}
