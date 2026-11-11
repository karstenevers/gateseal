"""Drive gateseal.cli.main in-process: arguments, output modes and error mapping.

The installed command reaches main only in a subprocess, which mutation testing
cannot see, so these tests call it directly against real Git repositories.
"""

import json
import sys
import urllib.request

import pytest
from conftest import named_flags
from gateseal.cli import main
from installation import package_version, success

HELP = "gateseal --help"


@pytest.fixture(scope="module")
def repository(tmp_path_factory):
    """An empty repository, which no test here changes."""
    root = tmp_path_factory.mktemp("cli repository")
    success(["git", "init", "--quiet", "--initial-branch=main"], root)
    return root


def gateseal(capsys, *arguments):
    """Return main's exit code and the JSON document it printed."""
    code = main([str(argument) for argument in arguments])
    return code, json.loads(capsys.readouterr().out)


def test_version_prints_the_package_release(capsys):
    with pytest.raises(SystemExit) as exit:
        main(["--version"])
    assert exit.value.code == 0
    assert capsys.readouterr().out == package_version() + "\n"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--repo", "{root}", "--json", "--verbose", "status"],
        ["status", "--repo", "{root}", "--json", "--verbose"],
        ["--json", "status", "--repo", "{root}"],
        ["--repo", "{root}", "status", "--json"],
    ],
    ids=["before", "after", "split json first", "split repo first"],
)
def test_options_work_before_and_after_the_command(
    capsys, repository, tmp_path, monkeypatch, arguments
):
    monkeypatch.chdir(tmp_path)
    filled = [a.format(root=repository) for a in arguments]
    code, result = gateseal(capsys, *filled)
    assert (code, result["command"], result["code"]) == (0, "status", "unconfigured")
    assert result["next_steps"] == ["gateseal configure --help"]


def test_without_a_command_or_repo_it_reports_the_current_directory(
    capsys, repository, monkeypatch
):
    monkeypatch.chdir(repository)
    code, result = gateseal(capsys, "--json")
    assert (code, result["command"], result["code"]) == (0, "status", "unconfigured")


def test_without_json_the_result_is_text(capsys, repository):
    assert main(["--repo", str(repository), "status"]) == 0
    out = capsys.readouterr().out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    assert out.endswith("\nNext: gateseal configure --help\n")


def test_main_reads_the_process_arguments(capsys, repository, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["gateseal", "--repo", str(repository), "--json"])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["code"] == "unconfigured"


def test_a_repository_outside_git_is_refused(capsys, tmp_path):
    code, result = gateseal(capsys, "--repo", tmp_path / "missing", "--json", "check")
    assert (code, result["command"], result["code"]) == (2, "check", "not_a_repository")
    assert [named_flags(step) for step in result["next_steps"]] == [{"--repo"}]


def test_an_unknown_option_is_an_invalid_invocation(capsys, repository):
    code, result = gateseal(capsys, "--json", "--repo", repository, "check", "--bogus")
    assert (code, result["status"], result["command"]) == (2, "failed", "check")
    assert result["code"] == "invalid_invocation"
    assert named_flags(result["diagnostics"][0]["message"]) == {"--bogus", "--help"}
    assert result["next_steps"] == []


def test_an_invalid_invocation_without_json_is_text(capsys, repository):
    assert main(["--repo", str(repository), "status", "--bogus"]) == 2
    out = capsys.readouterr().out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    assert "--bogus" in out
    assert HELP in out


def test_policy_needs_an_action(capsys):
    assert main(["policy"]) == 2
    out = capsys.readouterr().out
    assert "action" in out
    assert HELP in out


def test_an_unknown_policy_action_is_reported_as_policy(capsys):
    code, result = gateseal(capsys, "--json", "policy", "bogus", "lock")
    assert (code, result["command"], result["code"]) == (
        2,
        "policy",
        "invalid_invocation",
    )


def test_policy_init_needs_a_directory(capsys):
    code, result = gateseal(capsys, "--json", "policy", "init")
    assert (code, result["command"], result["code"]) == (
        2,
        "policy init",
        "invalid_invocation",
    )
    message = result["diagnostics"][0]["message"]
    assert "directory" in message
    assert HELP in message


def test_policy_actions_take_a_directory_not_a_repository(capsys, tmp_path):
    code, result = gateseal(
        capsys, "policy", "check", "--repo", tmp_path, tmp_path, "--json"
    )
    assert (code, result["command"], result["code"]) == (
        2,
        "policy check",
        "invalid_invocation",
    )
    assert named_flags(result["diagnostics"][0]["message"]) == {"--repo", "--help"}


@pytest.fixture
def started(capsys, tmp_path):
    directory = tmp_path / "team policy"
    code, result = gateseal(capsys, "policy", "init", directory, "--json")
    assert (code, result["command"], result["code"]) == (
        0,
        "policy init",
        "initialized",
    )
    assert {"policy.toml", "policy.lock", "README.md"} <= set(result["changed"])
    assert (directory / "policy.toml").is_file()
    return directory


def test_policy_check_defaults_to_the_current_directory(capsys, started, monkeypatch):
    monkeypatch.chdir(started)
    code, result = gateseal(capsys, "--json", "policy", "check")
    assert (code, result["command"], result["code"]) == (
        0,
        "policy check",
        "consistent",
    )
    assert gateseal(capsys, "--json", "policy", "check", started)[1]["code"] == (
        "consistent"
    )


def test_policy_pin_moves_the_named_component(capsys, started):
    code, result = gateseal(
        capsys, "--json", "policy", "pin", "pre-commit-hooks", "5.0.0", started
    )
    assert (code, result["command"], result["code"]) == (0, "policy pin", "pinned")
    assert result["changed"] == ["policy.toml"]
    text = (started / "policy.toml").read_text()
    assert '[components.pre-commit-hooks]\npin = "5.0.0"' in text


def test_policy_add_names_the_requested_component(capsys, started):
    lock = (started / "policy.lock").read_bytes()
    code, result = gateseal(
        capsys, "--json", "policy", "add", "nosuch", "1.0.0", started
    )
    assert (code, result["command"], result["code"]) == (
        2,
        "policy add",
        "unknown_component",
    )
    assert "nosuch" in result["diagnostics"][0]["message"]
    assert (started / "policy.lock").read_bytes() == lock


def test_a_filesystem_failure_exits_4(capsys, tmp_path):
    occupied = tmp_path / "file"
    occupied.write_text("not a directory\n")
    code, result = gateseal(capsys, "--json", "policy", "init", occupied / "policy")
    assert (code, result["command"], result["code"]) == (
        4,
        "policy init",
        "filesystem_error",
    )
    assert str(occupied / "policy") in result["diagnostics"][0]["message"]


class Interrupt(urllib.request.BaseHandler):
    """Stand in for Ctrl-C pressed while a download waits for the server."""

    handler_order = 100

    def https_open(self, request):
        raise KeyboardInterrupt


def test_an_interrupted_download_asks_for_a_retry(capsys, started, monkeypatch):
    monkeypatch.setattr(
        urllib.request, "_opener", urllib.request.build_opener(Interrupt)
    )
    lock = (started / "policy.lock").read_bytes()
    code, result = gateseal(capsys, "--json", "policy", "lock", started)
    assert (code, result["command"], result["code"]) == (
        4,
        "policy lock",
        "interrupted",
    )
    assert (started / "policy.lock").read_bytes() == lock
