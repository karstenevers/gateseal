"""One hook hands a report to the next at git push and in the gate.

A team policy adds two tools of the push stage to the bundled policy. The hook
of the first writes the pushed commit into a report, and the hook of the
second copies the report into the clone's Git directory. Both check the whole
project and start only for a Python file. Each tool is a hook repository with
one shell script, so no hook installs anything. Every push is a real git push
to a bare repository, through the launcher that pre-commit wrote.
"""

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import on_machine
from gateseal.validation import canonical
from installation import process, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

REPORT = ".gateseal/reports/head.txt"
HOOKS = "https://hooks.example/team/"
WRITER = ("report-writer", "write-report", "writes")
READER = ("report-reader", "read-report", "reads")
SCRIPTS = {
    "report-writer": '[ -n "$NO_REPORT" ] || echo "head $PRE_COMMIT_TO_REF" > "$1"\n',
    "report-reader": 'cat "$1" >> .git/read-report.log\n',
}
DEFINITION = """\
kind = "pre-commit-repository"
stages = ["push", "ci"]
scope = "project"

[source]
repository = "{repository}"
tag = "v{{version}}"

[hooks.{hook}]
name = "{hook}"
output = "plain"
args = ["{{head.txt}}"]
types_or = ["python"]
{declares} = ["head.txt"]
"""
TABLE = '\n[capabilities.reports.tools.{tool}]\npin = "1.0.0"\nreviewed = ["1.0.0"]\n'
SKIPPED = r"\.+\(no files to check\)Skipped"


def hook_repository(root, tool, hook):
    """Release a hook repository whose one hook runs the tool's script."""
    repository = root / tool
    repository.mkdir()
    git(repository, "init", "--quiet", "--initial-branch=main")
    script = repository / "run.sh"
    script.write_text("#!/bin/sh\n" + SCRIPTS[tool])
    script.chmod(0o755)
    (repository / ".pre-commit-hooks.yaml").write_text(
        f"- id: {hook}\n  name: {hook}\n  entry: run.sh\n  language: script\n"
    )
    git(repository, "add", "-A")
    git(repository, "commit", "--quiet", "-m", "Release 1.0.0")
    git(repository, "tag", "v1.0.0")
    return repository


def add_reports(repositories):
    """Return the change that offers both tools in the reports capability."""

    def change(data):
        (data / "tools").mkdir(exist_ok=True)
        lock = json.loads((data / "policy.lock").read_bytes())
        for tool, hook, declares in (WRITER, READER):
            (data / f"tools/{tool}.toml").write_text(
                DEFINITION.format(repository=HOOKS + tool, hook=hook, declares=declares)
            )
            with (data / "policy.toml").open("a") as policy:
                policy.write(TABLE.format(tool=tool))
            revision = git(repositories[tool], "rev-parse", "HEAD")
            lock["components"][tool] = {"versions": {"1.0.0": {"revision": revision}}}
        (data / "policy.lock").write_bytes(canonical(lock))

    return change


@dataclass(frozen=True)
class Reporting:
    """A clone whose pushes write and read the report, and its bare remote."""

    clone: Path
    remote: Path
    env: dict[str, str]

    def git(self, *arguments):
        return success(["git", *arguments], self.clone, self.env).stdout.strip()

    def push(self, name, text):
        """Commit one file and push it. Return the pushed commit and the output."""
        (self.clone / name).write_text(text)
        self.git("add", name)
        self.git("commit", "--quiet", "-m", f"Add {name}")
        pushed = process(["git", "push", "origin", "main"], self.clone, self.env)
        assert pushed.returncode == 0, pushed.stdout + pushed.stderr
        return self.git("rev-parse", "HEAD"), pushed.stdout + pushed.stderr

    def found_reports(self):
        """Return each report that the reading hook found, oldest first."""
        log = self.clone / ".git" / "read-report.log"
        return log.read_text().splitlines() if log.exists() else []


@pytest.fixture(scope="module")
def module_reporting(installed, tmp_path_factory):
    """Adopt the team policy's reports, install both launchers and push."""
    root = tmp_path_factory.mktemp("reporting consumer ü").resolve()
    repositories = {
        tool: hook_repository(root, tool, hook) for tool, hook, _ in (WRITER, READER)
    }
    origin = root / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", add_reports(repositories))
    served = {
        f"url.{path.as_uri()}.insteadOf": HOOKS + tool
        for tool, path in repositories.items()
    }
    env = environment(installed, origin, **served)
    remote = root / "remote.git"
    with on_machine(installed):
        success(["git", "init", "--quiet", "--bare", "-b", "main", remote], root)
        success(["git", "clone", "--quiet", remote, root / "clone"], root)
        reporting = Reporting(root / "clone", remote, env)
        reporting.git("config", "user.name", "gateseal test")
        reporting.git("config", "user.email", "gateseal@example.invalid")
        installed.cli(
            reporting.clone,
            "configure",
            *("--capability", "hygiene", "--capability", "reports"),
            *("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1"),
            env=env,
        )
        installed.cli(reporting.clone, "setup", "--refresh", env=env)
        reporting.git("add", "-A")
        reporting.push("app.py", "VALUE = 1\n")
    return reporting


@pytest.fixture
def reporting(module_reporting, tmp_path):
    """A copy of the adopted clone and its remote, whose reading hook has not run."""
    copy = Reporting(
        tmp_path / "clone ü", tmp_path / "remote.git", module_reporting.env
    )
    shutil.copytree(module_reporting.remote, copy.remote)
    shutil.copytree(module_reporting.clone, copy.clone, symlinks=True)
    copy.git("remote", "set-url", "origin", str(copy.remote))
    (copy.clone / ".git" / "read-report.log").unlink()
    return copy


def test_git_ignores_the_report_that_the_first_push_wrote(module_reporting):
    assert (module_reporting.clone / REPORT).read_text().startswith("head ")
    assert module_reporting.git("status", "--porcelain") == ""
    tracked = module_reporting.git("ls-files", ".gateseal/reports")
    assert tracked == ".gateseal/reports/.gitignore"


def test_a_push_runs_the_writing_hook_before_the_hook_that_reads_its_report(reporting):
    """The reading hook finds the commit of this push, not of the push before."""
    pushed, shown = reporting.push("more.py", "VALUE = 2\n")
    for _, hook, _ in (WRITER, READER):
        assert re.search(rf"{hook}\.+Passed", shown), shown
    assert reporting.found_reports() == [f"head {pushed}"]
    assert reporting.git("status", "--porcelain") == ""


def test_a_push_without_a_python_file_starts_neither_hook(reporting):
    _, shown = reporting.push("notes.txt", "ready\n")
    for _, hook, _ in (WRITER, READER):
        assert re.search(hook + SKIPPED, shown), shown
    assert reporting.found_reports() == []


def test_the_gate_passes_with_the_report_and_stops_without_it(installed, reporting):
    """The report of the push before the gate cannot pass for the gate's run."""
    gate = installed.cli(reporting.clone, "gate", env=reporting.env)
    assert gate["code"] == "checks_passed"
    assert reporting.found_reports() == ["head "]
    silent = dict(reporting.env, NO_REPORT="1")
    stopped = installed.cli(reporting.clone, "gate", code=2, env=silent)
    assert stopped["code"] == "missing_report"
    assert stopped["diagnostics"][0]["message"].startswith(
        f"Hook read-report ran, but {REPORT}, which write-report writes before it, "
        "is missing."
    )
    statuses = {
        entry["hook"]: entry["status"]
        for entry in stopped["diagnostics"]
        if entry["category"] == "hook" and entry["tool"].startswith("report-")
    }
    assert statuses == {"write-report": "passed", "read-report": "failed"}
    assert stopped["next_steps"]
    assert not (reporting.clone / REPORT).exists()
    assert reporting.found_reports() == ["head "]


def test_check_names_the_report_and_the_hook_that_writes_it(installed, reporting):
    checked = installed.cli(reporting.clone, "check", env=reporting.env)
    notes = [d["message"] for d in checked["diagnostics"] if d["category"] == "report"]
    assert notes == [
        f"Hook read-report reads {REPORT}, which write-report writes before it at a "
        "push and in the gate."
    ]
