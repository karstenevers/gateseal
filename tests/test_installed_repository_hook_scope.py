"""A project hook runs its script once, wherever the policy hooks run.

A consumer of the bundled policy adds two project hooks. api-types rebuilds
generated.txt from the files under schema/, and runs when one of them changes.
count runs at every commit. Each script logs its start and the number of file
names it got in the clone's Git folder, which no hook checks.
"""

import pytest
from conftest import on_machine
from installation import clone, process, success

pytestmark = pytest.mark.integration

GENERATOR = """#!/bin/sh
echo "api-types $#" >> "$(git rev-parse --git-dir)/starts.log"
cat schema/*.txt > generated.txt
"""
COUNTER = """#!/bin/sh
echo "count $#" >> "$(git rev-parse --git-dir)/starts.log"
"""
TABLES = """
[hooks.api-types]
name = "Rebuild generated.txt"
run = "scripts/api-types.sh"
files = "^schema/"
scope = "project"

[hooks.count]
name = "Count the runs"
run = "scripts/count.sh"
scope = "project"
"""
SCHEMA = [f"schema/{index:02}.txt" for index in range(20)]


@pytest.fixture(scope="module")
def module_generating(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("generating consumer ü").resolve()
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        for name, text in (("api-types.sh", GENERATOR), ("count.sh", COUNTER)):
            script = consumer / "scripts" / name
            script.parent.mkdir(exist_ok=True)
            script.write_text(text)
            script.chmod(0o755)
        (consumer / "schema").mkdir()
        (consumer / "schema/00.txt").write_text("first\n")
        (consumer / "generated.txt").write_text("first\n")
        installed.cli(consumer, "configure", "--capability", "hygiene")
        manifest = consumer / "gateseal.toml"
        manifest.write_text(manifest.read_text() + TABLES)
        installed.cli(consumer, "configure")
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-q", "--no-verify", "-m", "Adopt"], consumer)
        success(["git", "tag", "adopted"], consumer)
    return consumer


@pytest.fixture
def generating(module_generating):
    """The consumer at its adoption, with an empty log of starts."""
    log = module_generating / ".git/starts.log"
    log.unlink(missing_ok=True)
    yield module_generating
    success(["git", "reset", "-q", "--hard", "adopted"], module_generating)
    success(["git", "clean", "-fdq"], module_generating)


def starts(consumer):
    """Return the logged starts since the last call, and empty the log."""
    log = consumer / ".git/starts.log"
    lines = log.read_text().splitlines() if log.exists() else []
    log.unlink(missing_ok=True)
    return sorted(lines)


def write_schema(consumer):
    """Change 20 schema files and stage them with a fresh generated.txt."""
    for name in SCHEMA:
        (consumer / name).write_text(f"{name}\n")
    (consumer / "generated.txt").write_text("".join(f"{n}\n" for n in SCHEMA))
    success(["git", "add", "-A"], consumer)


def commit(consumer, message):
    committed = process(["git", "commit", "-q", "-m", message], consumer)
    return committed.returncode, committed.stdout + committed.stderr


def test_a_project_hook_runs_once_for_20_matching_files(installed, generating):
    write_schema(generating)
    result = installed.cli(generating, "run")
    assert result["code"] == "checks_passed", result
    assert starts(generating) == ["api-types 0", "count 0"]
    code, shown = commit(generating, "Change the schema")
    assert code == 0, shown
    assert starts(generating) == ["api-types 0", "count 0"]
    base = success(["git", "rev-parse", "adopted"], generating).stdout.strip()
    head = success(["git", "rev-parse", "HEAD"], generating).stdout.strip()
    gate = installed.cli(generating, "gate", "--base", base, "--head", head)
    assert gate["code"] == "checks_passed", gate
    assert starts(generating) == ["api-types 0", "count 0"]


def test_a_project_hook_with_files_starts_only_on_a_matching_change(
    installed, generating
):
    (generating / "notes.txt").write_text("notes\n")
    success(["git", "add", "notes.txt"], generating)
    code, shown = commit(generating, "Add notes")
    assert code == 0, shown
    assert starts(generating) == ["count 0"]
    base = success(["git", "rev-parse", "adopted"], generating).stdout.strip()
    head = success(["git", "rev-parse", "HEAD"], generating).stdout.strip()
    gate = installed.cli(generating, "gate", "--base", base, "--head", head)
    assert gate["code"] == "checks_passed", gate
    assert starts(generating) == ["count 0"]


def test_a_stale_generated_file_fails_the_commit_and_the_hook_rebuilds_it(
    installed, generating
):
    (generating / "schema/00.txt").write_text("changed\n")
    success(["git", "add", "schema/00.txt"], generating)
    code, shown = commit(generating, "Change the schema only")
    assert code == 1, shown
    assert "files were modified by this hook" in shown, shown
    assert (generating / "generated.txt").read_text() == "changed\n"
    assert starts(generating) == ["api-types 0", "count 0"]
    success(["git", "add", "generated.txt"], generating)
    code, shown = commit(generating, "Change the schema and its output")
    assert code == 0, shown
