"""gateseal gate reports its time, and warns past the policy's time budget.

A team policy's releases set time_budget_seconds to 3600 and to 1. One
consumer of the one-second release adds a repository hook that sleeps, so its
gate runs past the budget. The warning leaves the exit code, status and code
as the checks decide.
"""

import re

import pytest
from conftest import BUNDLED_MINIMUM, on_machine
from installation import clone, process, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

SLEEP = 2
SCRIPT = f'#!/bin/sh\nsleep {SLEEP}\n! grep -q FAIL "$@"\n'
TABLE = (
    "\n[hooks.slow]\n"
    'name = "Sleep, then refuse FAIL"\n'
    'run = "scripts/slow.sh"\n'
    'files = "\\\\.txt$"\n'
)
TOOK = "The gate took "
HEADING = "Time of each check, in seconds:"
SLOW = "Sleep, then refuse FAIL"
WARNING = (
    r"The gate took \d+\.\d seconds, over the policy's time budget of 1 seconds\. "
    r"Speed up its slowest checks, or raise time_budget_seconds in the policy\. "
    r"The slowest check took \d+\.\d seconds: Sleep, then refuse FAIL\."
)


def budget(seconds):
    def change(data):
        path = data / "policy.toml"
        text = path.read_text()
        assert BUNDLED_MINIMUM in text
        path.write_text(
            text.replace(
                BUNDLED_MINIMUM,
                f"{BUNDLED_MINIMUM}\ntime_budget_seconds = {seconds}",
                1,
            )
        )

    return change


@pytest.fixture(scope="module")
def team(installed, tmp_path_factory):
    origin = tmp_path_factory.mktemp("budget policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "roomy", budget(3600))
    publish(origin, "tight", budget(1))
    return environment(installed, origin)


def adopt(installed, root, env, tag=None, table=""):
    """Return a consumer of tag's release, or of the bundled policy without one."""
    policy = () if tag is None else ("--policy", URL, "--policy-path", "policy")
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        flags = (*policy, *(() if tag is None else ("--policy-tag", tag)))
        installed.cli(consumer, "configure", "--capability", "hygiene", *flags, env=env)
        if table:
            script = consumer / "scripts/slow.sh"
            script.parent.mkdir()
            script.write_text(SCRIPT)
            script.chmod(0o755)
            manifest = consumer / "gateseal.toml"
            manifest.write_text(manifest.read_text() + table)
            installed.cli(consumer, "configure", env=env)
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-q", "-m", "Adopt the policy"], consumer, env)
    return consumer


def diagnostics(result, category):
    return [d for d in result["diagnostics"] if d["category"] == category]


@pytest.mark.parametrize("tag,budget_seconds", [(None, None), ("roomy", 3600)])
def test_every_gate_reports_its_time(installed, team, tmp_path, tag, budget_seconds):
    consumer = adopt(installed, tmp_path, team, tag)
    result = installed.cli(consumer, "gate", env=team)
    [entry] = diagnostics(result, "duration")
    assert entry["severity"] == "info"
    assert not diagnostics(result, "over_time_budget")
    assert entry["seconds"] >= 0
    assert entry.get("budget_seconds") == budget_seconds
    assert entry["message"].startswith(TOOK)


@pytest.fixture(scope="module")
def slow(installed, team, tmp_path_factory):
    root = tmp_path_factory.mktemp("slow consumer ü").resolve()
    return adopt(installed, root, team, "tight", TABLE)


def gate_text(installed, consumer, env):
    return process([installed.command, "--repo", consumer, "gate"], consumer, env)


@pytest.mark.parametrize(
    "text,code,status,result_code",
    [("ready\n", 0, "ok", "checks_passed"), ("FAIL\n", 1, "failed", "checks_failed")],
    ids=["passing", "failing"],
)
def test_a_gate_past_its_budget_warns_and_keeps_its_result(
    installed, team, slow, text, code, status, result_code
):
    (slow / "notes.txt").write_text(text)
    success(["git", "add", "notes.txt"], slow)
    result = installed.cli(slow, "gate", env=team, code=code)
    assert (result["status"], result["code"]) == (status, result_code)
    [entry] = diagnostics(result, "duration")
    assert entry["seconds"] >= SLEEP
    assert entry["budget_seconds"] == 1
    [over] = diagnostics(result, "over_time_budget")
    assert over["severity"] == "warning"
    assert over["message"].startswith(TOOK)
    shown = gate_text(installed, slow, team)
    assert shown.returncode == code, shown.stdout + shown.stderr
    lines = shown.stdout.splitlines()
    [warning] = [line for line in lines if line.startswith(f"Warning: {TOOK}")]
    assert re.fullmatch(f"Warning: {WARNING}", warning), warning
    assert not [line for line in lines if line.startswith("::warning")]
    times = lines[lines.index(HEADING) + 1 : lines.index(warning)]
    *rows, last = times
    assert re.fullmatch(
        r" +\d+\.\d  pre-commit's own work, such as reading its configuration", rows[0]
    )
    [slow_row] = [row for row in rows if row.endswith(f"  {SLOW}")]
    assert float(slow_row.split()[0]) >= SLEEP
    assert re.fullmatch(
        rf"The gate took \d+\.\d seconds\. The slowest check took "
        rf"{slow_row.split()[0]} seconds: {SLOW}\.",
        last,
    ), last


@pytest.mark.parametrize(
    "variable,value,command",
    [
        ("GITHUB_ACTIONS", "true", "::warning title=gateseal::"),
        ("TF_BUILD", "True", "##vso[task.logissue type=warning]"),
    ],
    ids=["github actions", "azure pipelines"],
)
def test_in_ci_the_warning_is_the_providers_command(
    installed, team, slow, variable, value, command
):
    (slow / "notes.txt").write_text("ready\n")
    success(["git", "add", "notes.txt"], slow)
    shown = gate_text(installed, slow, dict(team, **{variable: value}))
    assert shown.returncode == 0, shown.stdout + shown.stderr
    lines = shown.stdout.splitlines()
    [warning] = [line for line in lines if line.startswith(command)]
    assert re.fullmatch(re.escape(command) + WARNING, warning), warning
    assert not [line for line in lines if line.startswith("Warning:")]


def test_a_gate_without_a_check_to_run_lists_no_time(installed, team, tmp_path):
    consumer = adopt(installed, tmp_path, team)
    head = success(["git", "rev-parse", "HEAD"], consumer).stdout.strip()
    revisions = ["--base", head, "--head", head]
    shown = process(
        [installed.command, "--repo", consumer, "gate", *revisions], consumer, team
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    message, *statuses, end = shown.stdout.splitlines()
    assert (message.startswith("Checks passed."), end) == (True, "")
    assert statuses
    assert all(
        re.fullmatch(r".+\.+\(no files to check\)Skipped", s) for s in statuses
    ), shown.stdout
