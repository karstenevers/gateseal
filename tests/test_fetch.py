import contextlib
import dataclasses
import io
import json
import os
from types import SimpleNamespace

import pytest
from conftest import TRACKED
from gateseal import checks, ci, fetch, lock, processes, setup
from gateseal.cli import main
from gateseal.diagnostics import GatesealError
from gateseal.platforms import machine
from gateseal.tools.release_binary import BinaryTool
from gateseal.worktree import locate_worktree, preparation_directory
from installation import success
from stand_ins import Installer, configure

OFFLINE = "mise ERROR Failed to install betterleaks: offline mode is enabled\n"
HOOKS = "https://github.com/pre-commit/pre-commit-hooks"
CLONED = f"[INFO] Initializing environment for {HOOKS}.\n"
BUILDING = "[INFO] Installing environment for https://example.invalid/team/hooks.\n"
STOPPED = "An unexpected error has occurred: CalledProcessError\n"
INSTALL_HOOKS = (
    "-I -B -m pre_commit install-hooks --color never --config .pre-commit-config.yaml"
)


@pytest.fixture(scope="module")
def selected(tmp_path_factory):
    directory = tmp_path_factory.mktemp("fetch")
    root = directory / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], directory)
    for key, value in (("user.name", "gateseal test"), ("user.email", "t@x.invalid")):
        success(["git", "config", key, value], root)
    worktree = locate_worktree(root)
    configure(worktree, ("hygiene", "secrets"))
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "--quiet", "-m", "Adopt"], root)
    locked = lock.load(worktree.root, preparation_directory(worktree))
    [betterleaks] = [t for t in locked.selection.tools if isinstance(t, BinaryTool)]
    return SimpleNamespace(
        directory=directory, worktree=worktree, locked=locked, betterleaks=betterleaks
    )


@pytest.fixture
def installers(selected, tmp_path, monkeypatch):
    mise = Installer(tmp_path / "mise bin" / "mise")
    pre_commit = Installer(tmp_path / "pre-commit" / "python")
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(mise.path.parent), os.environ["PATH"]])
    )
    monkeypatch.setattr(setup, "require_pre_commit", lambda: str(pre_commit.path))
    waits = []
    monkeypatch.setattr(fetch, "sleep", waits.append)
    return SimpleNamespace(mise=mise, pre_commit=pre_commit, waits=waits)


def fetched(locked):
    return fetch.fetch(locked, setup.require_pre_commit())


def archive(selected):
    tool = selected.betterleaks
    return f"betterleaks {tool.version}", tool.release.platforms[machine()].url


def failure(selected):
    with pytest.raises(GatesealError) as caught:
        fetched(selected.locked)
    return caught.value


def test_fetch_installs_each_release_binary_and_builds_the_hook_environments(
    selected, installers
):
    result = fetch.run(selected.locked)
    name, url = archive(selected)
    assert (result.command, result.code, result.exit_code) == ("fetch", "fetched", 0)
    assert result.message == (
        "The selected release binaries and the hook environments are installed."
    )
    assert installers.mise.calls() == [
        f"--no-config install {selected.betterleaks.mise_request}"
    ]
    assert installers.pre_commit.calls() == [INSTALL_HOOKS]
    assert result.entries == (
        {
            "severity": "info",
            "category": "download",
            "attempts": 1,
            "message": f"mise installed {name} from {url}.",
        },
        {
            "severity": "info",
            "category": "download",
            "attempts": 1,
            "message": "pre-commit built the hook environments of "
            ".pre-commit-config.yaml.",
        },
    )
    assert result.details.splitlines() == [e["message"] for e in result.entries]
    assert installers.waits == []


def test_each_download_runs_in_the_clone_with_the_callers_environment(
    selected, installers, monkeypatch
):
    monkeypatch.setenv("MISE_DATA_DIR", "/a/data folder")
    fetched(selected.locked)
    for stand_in in (installers.mise, installers.pre_commit):
        assert stand_in.saw() == (str(selected.locked.root), "/a/data folder")


def test_a_download_that_fails_once_passes_at_the_second_attempt(selected, installers):
    installers.mise.fail(1, OFFLINE)
    first, second = fetched(selected.locked)
    assert first["attempts"] == 2
    assert first["message"].endswith(" The download took 2 attempts.")
    assert (second["attempts"], installers.waits) == (1, [1])
    assert len(installers.mise.calls()) == 2


def test_a_download_that_fails_twice_passes_at_the_last_attempt(selected, installers):
    installers.pre_commit.fail(2, STOPPED, 3)
    _, hooks = fetched(selected.locked)
    assert (hooks["attempts"], installers.waits) == (3, [1, 3])


def test_a_release_binary_that_cannot_be_downloaded_fails_closed(selected, installers):
    installers.mise.fail(3, OFFLINE)
    failed = failure(selected)
    name, url = archive(selected)
    assert (failed.code, failed.exit_code) == ("download_failed", 4)
    assert str(failed) == (
        f"mise could not install {name} from {url} in 3 attempts, so the code was "
        "not checked."
    )
    assert failed.details == OFFLINE
    assert "again" in failed.next_step
    assert (len(installers.mise.calls()), installers.waits) == (3, [1, 3])
    assert installers.pre_commit.calls() == []


def test_a_hook_environment_that_cannot_be_built_names_its_repository(
    selected, installers
):
    installers.pre_commit.fail(3, CLONED + BUILDING + STOPPED, 3)
    failed = failure(selected)
    assert (failed.code, failed.exit_code) == ("download_failed", 4)
    assert str(failed) == (
        "pre-commit could not build the hook environment of "
        "https://example.invalid/team/hooks in 3 attempts, so the code was not "
        "checked."
    )
    assert failed.details == CLONED + BUILDING + STOPPED
    assert len(installers.pre_commit.calls()) == 3


def test_a_failure_shows_what_the_installer_printed_and_its_errors(
    selected, installers
):
    installers.pre_commit.fail(3, CLONED + STOPPED, 3, errors="fatal: no route\n")
    failed = failure(selected)
    assert f"the hook environment of {HOOKS} in" in str(failed)
    assert failed.details == CLONED + STOPPED + "fatal: no route\n"


def test_a_failure_before_any_environment_names_the_configuration(selected, installers):
    installers.pre_commit.fail(3, STOPPED, 1)
    assert str(failure(selected)).startswith(
        "pre-commit could not build the hook environments of .pre-commit-config.yaml in"
    )


def without_release_binaries(locked):
    tools = tuple(t for t in locked.selection.tools if not isinstance(t, BinaryTool))
    selection = dataclasses.replace(locked.selection, tools=tools)
    return dataclasses.replace(locked, selection=selection)


def test_a_selection_without_a_release_binary_needs_no_mise(selected, installers):
    installers.mise.path.unlink()
    [hooks] = fetched(without_release_binaries(selected.locked))
    assert hooks["message"].startswith("pre-commit built")


def test_a_release_binary_needs_mise(selected, installers):
    installers.mise.path.write_text("#!/bin/sh\necho 2026.10.18 linux-x64\n")
    failed = failure(selected)
    assert (failed.code, failed.exit_code) == ("mise_version_mismatch", 4)
    assert installers.pre_commit.calls() == []


@pytest.mark.parametrize("found", [None, "linux-arm64"])
def test_a_machine_without_an_archive_stops_before_any_download(
    selected, installers, monkeypatch, found
):
    monkeypatch.setattr(fetch, "machine", lambda: found)
    failed = failure(selected)
    name, _ = archive(selected)
    assert (failed.code, failed.exit_code) == ("unsupported_machine", 3)
    assert str(failed) == (
        f"{name} has no archive for this machine, so the code was not checked."
    )
    assert failed.next_step == (
        f"Run the checks on a machine that {name} has an archive for: "
        "linux-x64, macos-arm64."
    )
    assert installers.mise.calls() == installers.pre_commit.calls() == []


def test_a_machine_without_an_archive_needs_no_mise(selected, installers, monkeypatch):
    installers.mise.path.write_text("#!/bin/sh\necho 2026.10.18 linux-x64\n")
    monkeypatch.setattr(fetch, "machine", lambda: None)
    assert failure(selected).code == "unsupported_machine"


def gate(selected):
    return checks.gate(selected.worktree, selected.locked, ci.full_scope())


def test_the_gate_makes_every_download_before_it_runs_a_hook(selected, installers):
    assert gate(selected).code == "checks_passed"
    assert len(installers.mise.calls()) == 1
    installed, ran = installers.pre_commit.calls()
    assert installed == INSTALL_HOOKS
    assert " run " in ran


def test_a_failed_download_stops_the_gate_before_any_hook(selected, installers):
    installers.mise.fail(3, OFFLINE)
    with pytest.raises(GatesealError) as caught:
        gate(selected)
    assert caught.value.code == "download_failed"
    assert installers.pre_commit.calls() == []


def test_the_gates_time_leaves_out_the_downloads(selected, installers, monkeypatch):
    installers.mise.fail(2, OFFLINE)
    monkeypatch.setattr(processes, "monotonic", lambda: float(sum(installers.waits)))
    assert gate(selected).entries[0]["seconds"] == 0.0
    assert installers.waits == [1, 3]


def command(selected, *arguments):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(selected.locked.root), *arguments])
    return code, output.getvalue()


def test_the_fetch_command_reports_each_download(selected, installers):
    code, printed = command(selected, "--json", "fetch")
    result = json.loads(printed)
    assert (code, result["command"], result["code"]) == (0, "fetch", "fetched")
    downloads = [d for d in result["diagnostics"] if d["category"] == "download"]
    assert [d["attempts"] for d in downloads] == [1, 1]


@pytest.mark.parametrize("name", ["fetch", "gate"])
def test_a_command_whose_download_fails_shows_the_installers_output(
    selected, installers, name
):
    installers.mise.fail(3, OFFLINE)
    _, url = archive(selected)
    code, printed = command(selected, "--json", name)
    result = json.loads(printed)
    assert (code, result["command"], result["code"]) == (4, name, "download_failed")
    assert [d["message"] for d in result["diagnostics"]] == [
        f"mise could not install betterleaks {selected.betterleaks.version} from "
        f"{url} in 3 attempts, so the code was not checked.",
        OFFLINE,
    ]
    assert result["next_steps"] == [fetch.RERUN]
    installers.mise.fail(3, OFFLINE)
    code, printed = command(selected, name)
    assert code == 4
    assert printed.splitlines()[1:] == [OFFLINE.strip(), "", f"Next: {fetch.RERUN}"]
