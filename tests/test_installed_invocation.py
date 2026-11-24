"""The installed command's result names the command that failed, even when a
relative repository path or an option value equals another command's name."""

import json

import pytest
from conftest import COMMANDS
from installation import process

NAMES = (*COMMANDS, "policy")
"""Each name that the parser accepts as a command, so each one is a value once."""


def gateseal(installed, cwd, *arguments):
    result = process(
        [installed.command, "--json", *arguments], cwd, installed.environment
    )
    return result.returncode, json.loads(result.stdout)


@pytest.mark.parametrize("command", [*COMMANDS, "policy lock"])
def test_an_option_value_that_names_a_command_is_not_the_command(
    installed, tmp_path, command
):
    value = NAMES[NAMES.index(command.split()[0]) - 1]
    code, result = gateseal(
        installed, tmp_path, "--repo", value, *command.split(), "--bogus"
    )
    assert (code, result["command"], result["code"]) == (
        2,
        command,
        "invalid_invocation",
    )


@pytest.mark.parametrize("command", COMMANDS)
def test_a_repository_path_that_names_a_command_is_not_the_command(
    installed, tmp_path, command
):
    path = NAMES[NAMES.index(command) + 1]
    refresh = ["--refresh"] if command == "setup" else []
    code, result = gateseal(installed, tmp_path, "--repo", path, command, *refresh)
    assert (code, result["command"], result["code"]) == (
        2,
        command,
        "not_a_repository",
    )


def test_an_unknown_command_names_no_command(installed, tmp_path):
    code, result = gateseal(installed, tmp_path, "bogus")
    assert (code, result["command"], result["code"]) == (
        2,
        None,
        "invalid_invocation",
    )
