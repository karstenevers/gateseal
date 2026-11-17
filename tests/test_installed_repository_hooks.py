"""A repository hook runs a script of the repository wherever the policy hooks run.

A consumer of the bundled policy adds a [hooks] table whose script refuses a
text file that says DO-NOT-COMMIT. A commit, gateseal run and gateseal gate run
pre-commit on the same generated configuration, so all three run the script.
"""

import pytest
from conftest import on_machine
from installation import clone, process, success

pytestmark = pytest.mark.integration

SCRIPT = """#!/bin/sh
for file in "$@"; do
  if grep -q DO-NOT-COMMIT "$file"; then
    echo "$file says DO-NOT-COMMIT"
    exit 1
  fi
done
"""
TABLE = (
    "\n[hooks.no-markers]\n"
    'name = "Refuse DO-NOT-COMMIT"\n'
    'run = "scripts/no-markers.sh"\n'
    'files = "\\\\.txt$"\n'
)


@pytest.fixture(scope="module")
def module_hooked(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("hooked consumer ü").resolve()
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        script = consumer / "scripts/no-markers.sh"
        script.parent.mkdir()
        script.write_text(SCRIPT)
        script.chmod(0o755)
        installed.cli(consumer, "configure", "--capability", "hygiene")
        manifest = consumer / "gateseal.toml"
        manifest.write_text(manifest.read_text() + TABLE)
        installed.cli(consumer, "configure")
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-m", "Add a repository hook"], consumer)
        success(["git", "tag", "adopted"], consumer)
    return consumer


@pytest.fixture
def hooked(module_hooked):
    """The consumer, reset to its adoption after each test."""
    yield module_hooked
    success(["git", "reset", "-q", "--hard", "adopted"], module_hooked)
    success(["git", "clean", "-fdq"], module_hooked)


@pytest.mark.parametrize(("text", "code"), [("ready\n", 0), ("DO-NOT-COMMIT\n", 1)])
def test_commit_run_and_gate_run_the_repository_hook(installed, hooked, text, code):
    (hooked / "notes.txt").write_text(text)
    success(["git", "add", "notes.txt"], hooked)
    for command in ("run", "gate"):
        result = installed.cli(hooked, command, code=code)
        (entry,) = [d for d in result["diagnostics"] if d.get("hook") == "no-markers"]
        assert (entry["origin"], entry["tool"], entry["status"]) == (
            "repository",
            "scripts/no-markers.sh",
            "failed" if code else "passed",
        )
        assert bool(result["next_steps"]) == bool(code)
    committed = process(["git", "commit", "-m", "Add notes"], hooked)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == code, shown
    assert ("notes.txt says DO-NOT-COMMIT" in shown) == bool(code), shown


def test_status_lists_the_repository_hook(installed, hooked):
    diagnostics = installed.cli(hooked, "status")["diagnostics"]
    listed = [d["message"] for d in diagnostics if d["category"] == "repository_hook"]
    assert listed == [
        "Repository hook no-markers: scripts/no-markers.sh, on files matching \\.txt$."
    ]


def test_an_edited_hook_or_hooks_table_stops_check_and_commit(installed, hooked):
    config = hooked / ".pre-commit-config.yaml"
    config.write_bytes(
        config.read_bytes().replace(b"Refuse DO-NOT-COMMIT", b"Refuse nothing")
    )
    assert installed.cli(hooked, "check", code=2)["code"] == "ownership_conflict"
    success(["git", "add", config.name], hooked)
    committed = process(["git", "commit", "-m", "Edit the hook"], hooked)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == 1, shown
    assert (
        "gateseal: The staged .pre-commit-config.yaml differs from the file gateseal "
        "wrote." in shown
    ), shown
    success(["git", "reset", "-q", "--hard", "adopted"], hooked)
    manifest = hooked / "gateseal.toml"
    manifest.write_text(manifest.read_text().replace("Refuse DO", "Stop DO"))
    assert installed.cli(hooked, "check", code=2)["code"] == "lock_mismatch"
    success(["git", "add", manifest.name], hooked)
    committed = process(["git", "commit", "-m", "Rename the hook"], hooked)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == 1, shown
    assert (
        "gateseal: The staged gateseal.toml is not the one that gateseal.lock was "
        "compiled from." in shown
    ), shown
