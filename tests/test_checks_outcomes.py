"""What gateseal run and gate report for each way pre-commit and a scan end.

The real pre-commit needs uv to install it and mise a downloaded Betterleaks,
so stand-ins take their place: shell scripts that record their arguments,
print the output a test gives them and exit with the test's status. One is the
Python of the owned pre-commit, the other is the mise on the PATH. The policy,
the worktree, its Git, the scope planning and the processes are real.
"""

import dataclasses
import json
import os
from types import SimpleNamespace

import pytest
from conftest import TRACKED
from gateseal import checks, ci, lock, processes, setup
from gateseal.diagnostics import GatesealError, Note
from gateseal.worktree import locate_worktree, preparation_directory
from installation import success
from stand_ins import configure

COMMIT = "c" * 40
YAML_PASSED = "check yaml...............................................Passed\n"
WHITESPACE_FIXED = (
    "trim trailing whitespace.................................Failed\n"
    "- hook id: trailing-whitespace\n"
    "- exit code: 1\n"
    "- files were modified by this hook\n"
    "\n"
    "Fixing notes.txt\n"
)
SECRET_FOUND = (
    "Detect secrets with Betterleaks..........................Failed\n"
    "- hook id: betterleaks\n"
    "- exit code: 1\n"
    "\n"
    "keys.env:1:5: generic-api-key (Generic API key)\n"
)
EOF_FAILED = (
    "fix end of files.........................................Failed\n"
    "- hook id: end-of-file-fixer\n"
    "- exit code: 1\n"
)
HISTORY_FINDING = f"leak.txt:1:7: aws-access-token (AWS) in commit {COMMIT}\n"
CURRENT_FINDING = "leak.txt:1:7: aws-access-token (AWS)\n"
FIX = "finding"
RECOMMIT = "gateseal run"
READ = "output of each failed hook"
REVIEW = "git diff"
STAGE = "gateseal run"


class Tool:
    """A stand-in executable that records its arguments and answers as told."""

    def __init__(self, state, name):
        state.mkdir()
        self.arguments = state / "arguments"
        self.output = state / "output"
        self.status = state / "status"
        self.executable = state / name
        self.executable.write_text(
            "#!/bin/sh\n"
            f"printf '%s\\n' \"$@\" > '{self.arguments}'\n"
            f"cat '{self.output}'\n"
            "echo 'a warning' >&2\n"
            f"exit \"$(cat '{self.status}')\"\n"
        )
        self.executable.chmod(0o755)
        self.reset()

    def reset(self):
        self.arguments.unlink(missing_ok=True)
        self.answer("", 0)

    def answer(self, output, status):
        self.output.write_text(output)
        self.status.write_text(str(status))

    def ran(self):
        return self.arguments.read_text().splitlines() if self.has_run() else None

    def has_run(self):
        return self.arguments.exists()


def commit(root, name):
    (root / name).write_text(name + "\n")
    success(["git", "add", name], root)
    success(["git", "commit", "--quiet", "-m", name], root)
    return success(["git", "rev-parse", "HEAD"], root).stdout.strip()


@pytest.fixture(scope="module")
def planned(tmp_path_factory):
    """A clone whose main adds one commit to its adopted base, and the gate's inputs."""
    directory = tmp_path_factory.mktemp("gate")
    root = directory / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], directory)
    for key, value in (("user.name", "gateseal test"), ("user.email", "t@x.invalid")):
        success(["git", "config", key, value], root)
    worktree = locate_worktree(root)
    configure(worktree, ("hygiene", "secrets"))
    success(["git", "add", *TRACKED], root)
    base, head = commit(root, "base.txt"), commit(root, "notes.txt")
    local = preparation_directory(worktree)
    return SimpleNamespace(
        root=root,
        worktree=worktree,
        locked=lock.load(worktree.root, local),
        scope=ci.change_scope(worktree, base, head),
        base=base,
        head=head,
        attempt=SimpleNamespace(
            pre_commit=Tool(directory / "pre-commit", "python"),
            scanner=Tool(directory / "scanner", "mise"),
        ),
    )


@pytest.fixture
def attempt(planned, monkeypatch):
    """Return the stand-in pre-commit and mise, reset to answer "", 0."""
    stand_ins = planned.attempt
    stand_ins.pre_commit.reset()
    stand_ins.scanner.reset()
    python = str(stand_ins.pre_commit.executable)
    monkeypatch.setattr(setup, "require_pre_commit", lambda: python)
    path = os.pathsep.join(
        [str(stand_ins.scanner.executable.parent), os.environ["PATH"]]
    )
    monkeypatch.setenv("PATH", path)
    return stand_ins


def gate(planned, scope=None):
    return checks.gate(planned.worktree, planned.locked, scope or planned.scope)


def history(result):
    return [entry for entry in result.entries if entry["category"] == "history"]


def steps_name(result, tokens):
    """Check that result's next steps name tokens, one step for each, in order."""
    pairs = zip(result.next_steps, tokens, strict=True)
    assert all(token in step for step, token in pairs)


def test_a_change_checks_its_files_and_scans_its_commits(attempt, planned):
    attempt.pre_commit.answer(YAML_PASSED, 0)
    result = gate(planned)
    assert (result.code, result.exit_code) == ("checks_passed", 0)
    assert planned.scope.reason in result.message
    revisions = f"{planned.base}..{planned.head}"
    assert attempt.pre_commit.ran()[-6:] == [
        "--from-ref",
        planned.base,
        "--to-ref",
        planned.head,
        "--color",
        "never",
    ]
    [betterleaks] = [t for t in planned.locked.selection.tools if t.program()]
    scan = attempt.scanner.ran()
    program = betterleaks.program()
    assert scan[: len(program) + 1] == [
        "--no-config",
        "exec",
        "--deny-env",
        "--allow-env",
        "GIT_NO_LAZY_FETCH",
        *program[4:],
    ]
    assert scan[-1].endswith(f" {revisions}")
    [scan] = history(result)
    assert revisions in scan.pop("message")
    assert scan == {
        "severity": "info",
        "category": "history",
        "tool": "betterleaks",
        "origin": "built-in",
        "stage": "ci",
        "status": "passed",
    }
    assert result.details == YAML_PASSED + "a warning\n" + "a warning\n"


def test_every_file_is_checked_without_a_history_scan(attempt, planned):
    result = gate(planned, ci.full_scope())
    assert result.code == "checks_passed"
    assert "--all-files" in attempt.pre_commit.ran()
    assert not attempt.scanner.has_run()
    assert history(result) == []


def test_a_deleted_ref_runs_nothing(attempt, planned):
    result = checks.gate(planned.worktree, planned.locked, None)
    assert (result.code, result.exit_code) == ("nothing_to_check", 0)
    assert not attempt.pre_commit.has_run()
    assert result.entries == ()


def timed_as(monkeypatch, pre_commit, scan, hooks):
    """Make the gate see pre-commit, each named hook and a scan take these seconds."""

    def execute_timed(arguments, root, env):
        timed = processes.execute_timed(arguments, root, env)
        if "pre_commit" not in arguments:
            return dataclasses.replace(timed, seconds=scan)
        lines = tuple(
            (line, hooks.get(line.split(b".")[0].decode(), seconds))
            for line, seconds in timed.lines
        )
        return dataclasses.replace(timed, seconds=pre_commit, lines=lines)

    monkeypatch.setattr(checks, "execute_timed", execute_timed)


SETUP = "pre-commit setup, such as installing hooks"


@pytest.mark.parametrize(
    "output,status,rows,slowest",
    [
        (
            YAML_PASSED,
            0,
            ["4.0  " + SETUP, "1.0  check yaml"],
            "3.0 seconds: betterleaks history scan",
        ),
        (
            YAML_PASSED + EOF_FAILED,
            1,
            ["0.5  " + SETUP, "1.0  check yaml", "3.5  fix end of files"],
            "3.5 seconds: fix end of files",
        ),
    ],
    ids=["passing", "failing"],
)
def test_a_gate_lists_the_time_of_each_check_and_names_the_slowest(
    attempt, planned, monkeypatch, output, status, rows, slowest
):
    attempt.pre_commit.answer(output, status)
    timed_as(monkeypatch, 5.0, 3.0, {"check yaml": 1.0, "fix end of files": 3.5})
    result = gate(planned)
    took = f"The gate took 8.0 seconds. The slowest check took {slowest}."
    assert result.check_times == (
        "Time of each check, in seconds:",
        *(f"    {row}" for row in rows),
        "    3.0  betterleaks history scan",
        took,
    )
    assert result.entries[0] == {
        "severity": "info",
        "category": "duration",
        "seconds": 8.0,
        "message": took,
    }
    assert result.exit_code == status


@pytest.mark.parametrize(
    "output,status,code",
    [(YAML_PASSED, 0, "checks_passed"), (EOF_FAILED + YAML_PASSED, 1, "checks_failed")],
    ids=["passing", "failing"],
)
def test_a_gate_over_its_budget_warns_and_ends_as_its_checks_decide(
    attempt, planned, monkeypatch, output, status, code
):
    attempt.pre_commit.answer(output, status)
    timed_as(monkeypatch, 299.0, 1.06, {"check yaml": 250.1})
    policy = dataclasses.replace(planned.locked.policy, time_budget_seconds=300)
    locked = dataclasses.replace(planned.locked, policy=policy)
    result = checks.gate(planned.worktree, locked, planned.scope)
    assert (result.entries[0]["seconds"], result.entries[0]["budget_seconds"]) == (
        300.1,
        300,
    )
    warning = (
        "The gate took 300.1 seconds, over the policy's time budget of 300 seconds. "
        "Speed up its slowest checks, or raise time_budget_seconds in the policy. "
        "The slowest check took 250.1 seconds: check yaml."
    )
    assert result.notes == (Note("warning", "over_time_budget", warning),)
    assert (result.code, result.exit_code) == (code, status)
    assert json.loads(result.render(True))["status"] == ("failed" if status else "ok")
    assert f"Warning: {warning}" in result.render(False).splitlines()


def test_a_gate_without_a_check_to_run_reads_as_before(attempt, planned):
    attempt.pre_commit.answer(
        "check yaml..............................(no files to check)Skipped\n", 0
    )
    result = gate(planned, ci.full_scope())
    assert result.check_times == ()
    seconds = result.entries[0]["seconds"]
    assert result.entries[0]["message"] == f"The gate took {seconds} seconds."
    assert result.render(False) == f"{result.message}\n{result.details}"


def test_a_head_that_is_not_checked_out_stops_before_pre_commit(attempt, planned):
    success(["git", "checkout", "--quiet", "--detach", planned.base], planned.root)
    try:
        with pytest.raises(GatesealError) as failure:
            gate(planned)
    finally:
        success(["git", "checkout", "--quiet", "main"], planned.root)
    assert failure.value.code == "head_not_checked_out"
    assert not attempt.pre_commit.has_run()


def test_a_finding_in_the_history_fails_the_gate(attempt, planned):
    attempt.scanner.answer(CURRENT_FINDING + HISTORY_FINDING, 1)
    result = gate(planned)
    assert (result.code, result.exit_code) == ("checks_failed", 1)
    assert planned.scope.reason in result.message
    steps_name(result, (FIX,))
    [scan] = history(result)
    assert (scan["severity"], scan["status"]) == ("error", "failed")
    assert f"{planned.base}..{planned.head}" in scan["message"]
    findings = [entry for entry in result.entries if entry["category"] == "finding"]
    assert [(f["file"], f["line"], f["column"], f["commit"]) for f in findings] == [
        ("leak.txt", 1, 7, COMMIT)
    ]
    assert (findings[0]["hook"], findings[0]["message"]) == ("history", "(AWS)")


@pytest.mark.parametrize(
    "output,status",
    [(HISTORY_FINDING, 0), ("", 1), (HISTORY_FINDING, 2)],
    ids=["finding with success", "failure without finding", "error with finding"],
)
def test_a_scan_that_ends_otherwise_stopped(attempt, planned, output, status):
    attempt.scanner.answer(output, status)
    result = gate(planned)
    assert (result.code, result.exit_code) == ("tool_failed", 4)
    assert "history" in result.message
    assert planned.scope.reason in result.message
    assert result.next_steps == ("gateseal doctor",)
    [scan] = history(result)
    assert (scan["severity"], scan["status"]) == ("error", "failed")
    assert f"{planned.base}..{planned.head}" in scan["message"]


@pytest.mark.parametrize("status", [0, 1])
def test_a_stopped_scan_outranks_the_hooks(attempt, planned, status):
    attempt.pre_commit.answer(SECRET_FOUND, status)
    attempt.scanner.answer("", 1)
    result = gate(planned)
    assert result.code == "tool_failed"
    assert "history" in result.message


def test_pre_commit_stopping_is_a_tool_failure(attempt, planned):
    attempt.pre_commit.answer("An unexpected error\n", 3)
    result = gate(planned)
    assert (result.code, result.exit_code) == ("tool_failed", 4)
    assert "status 3" in result.message
    assert planned.scope.reason in result.message
    assert result.next_steps == ("gateseal doctor",)


@pytest.mark.parametrize(
    "output,steps",
    [
        (YAML_PASSED + SECRET_FOUND + WHITESPACE_FIXED, (FIX, RECOMMIT)),
        (YAML_PASSED + WHITESPACE_FIXED, (RECOMMIT,)),
        (YAML_PASSED + SECRET_FOUND, (FIX,)),
        (YAML_PASSED + EOF_FAILED, (READ,)),
    ],
    ids=["finding and fix", "fix", "finding", "failure"],
)
def test_failed_hooks_name_the_steps_their_results_need(
    attempt, planned, output, steps
):
    attempt.pre_commit.answer(output, 1)
    result = gate(planned)
    assert (result.code, result.exit_code) == ("checks_failed", 1)
    steps_name(result, steps)


def run(planned):
    return checks.run(planned.locked)


def test_run_passes_when_pre_commit_does(attempt, planned):
    attempt.pre_commit.answer(YAML_PASSED, 0)
    result = run(planned)
    assert (result.code, result.exit_code) == ("checks_passed", 0)
    assert "--all-files" in attempt.pre_commit.ran()
    assert result.details == YAML_PASSED + "a warning\n"
    assert [entry["hook"] for entry in result.entries] == ["check-yaml"]


@pytest.mark.parametrize(
    "output,steps",
    [
        (YAML_PASSED + SECRET_FOUND + WHITESPACE_FIXED, (FIX, REVIEW, STAGE)),
        (YAML_PASSED + EOF_FAILED, (STAGE,)),
    ],
    ids=["finding and fix", "failure"],
)
def test_run_names_the_steps_its_failures_need(attempt, planned, output, steps):
    attempt.pre_commit.answer(output, 1)
    result = run(planned)
    assert (result.code, result.exit_code) == ("checks_failed", 1)
    steps_name(result, steps)


def test_run_names_a_fixing_hook_and_what_it_did(attempt, planned):
    attempt.pre_commit.answer(WHITESPACE_FIXED, 1)
    [fixed] = run(planned).entries
    assert fixed["modified"] is True
    assert "trim trailing whitespace" in fixed["message"]
    assert "files were modified by this hook" in fixed["message"]


def test_run_reports_pre_commit_stopping(attempt, planned):
    attempt.pre_commit.answer("An unexpected error\n", 3)
    result = run(planned)
    assert (result.code, result.exit_code) == ("tool_failed", 4)
    assert "status 3" in result.message
    assert result.next_steps == ("gateseal doctor",)
    assert result.details == "An unexpected error\na warning\n"
