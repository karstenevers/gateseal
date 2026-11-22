"""In CI, the gate's log shows the diff of what its hooks changed, redacted.

A CI run cannot show its reader the changed files, so the gate prints Git's
diff after pre-commit's output. A local gate leaves the change in the worktree,
where git diff shows it.
"""

import pytest
from gate_runs import (
    AZURE,
    EOF_FAILED,
    GITHUB,
    WHITESPACE_FIXED,
    YAML_PASSED,
    gateseal,
    stand_in,
)
from gateseal.diagnostics import COLOR

HEADER = "Changes that the hooks made:"
EDIT = "printf 'changed\\n' > notes.txt"
HALF = "A1b2C3d4E5f6G7h8I9j0"


@pytest.fixture(scope="module")
def configured(tmp_path_factory):
    return stand_in(tmp_path_factory.mktemp("ci diff"))


@pytest.fixture
def clone(configured, monkeypatch):
    configured.use(monkeypatch)
    yield configured
    configured.reset()


def lines(shown):
    return COLOR.sub("", shown).splitlines()


@pytest.mark.parametrize("env", [GITHUB, AZURE], ids=["github", "azure"])
def test_in_ci_the_log_shows_what_a_hook_changed(clone, monkeypatch, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    clone.answer(YAML_PASSED + WHITESPACE_FIXED, 1, EDIT)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    plain = lines(shown)
    at = plain.index(HEADER)
    assert plain[at - 2 : at] == ["Fixing notes.txt", ""]
    assert {"-original", "+changed"} <= set(plain[at:])
    assert "\x1b[" in shown.partition(HEADER)[2]


def test_a_local_gate_prints_no_diff(clone):
    clone.answer(WHITESPACE_FIXED, 1, EDIT)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert (clone.root / "notes.txt").read_text() == "changed\n"
    assert HEADER not in shown
    assert "+changed" not in lines(shown)


def test_a_failed_gate_without_a_modified_hook_prints_no_diff(clone, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    clone.answer(EOF_FAILED, 1)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert HEADER not in shown


def test_a_gate_that_starts_with_changes_shows_no_diff(clone, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    (clone.root / "notes.txt").write_text("edited before the gate\n")
    clone.answer(WHITESPACE_FIXED, 1, EDIT)
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert HEADER not in shown
    assert "+changed" not in lines(shown)
    assert (
        "The worktree had changes before the hooks ran, so the log shows no diff of "
        "what the hooks changed."
    ) in lines(shown)


def test_azure_runs_no_logging_command_in_a_changed_line(clone, monkeypatch):
    monkeypatch.setenv("TF_BUILD", "True")
    command = "##vso[task.setvariable variable=x]y"
    clone.answer(WHITESPACE_FIXED, 1, f"printf '{command}\\n' > notes.txt")
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert "+##vso [task.setvariable variable=x]y" in lines(shown)
    assert command not in shown


def test_a_secret_on_a_changed_line_is_redacted(clone, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    clone.answer(
        WHITESPACE_FIXED, 1, f"printf 'changed ghp_{HALF}{HALF}  \\n' > notes.txt"
    )
    code, shown = gateseal(clone.root, "gate")
    assert code == 1
    assert HALF not in shown
    assert "+changed [redacted]  " in lines(shown)
