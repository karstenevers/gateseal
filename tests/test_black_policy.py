"""A team switches its formatter to Black on a newer Python, as add-a-tool.md shows.

Two releases of a team policy offer the how-to's Black alternative, and only
the second pins the Hook Python, CPython 3.15.2. A consumer of each picks Black
in gateseal.toml and commits a file with an explicit lazy import, which only
CPython 3.15 parses. Black formats it on the Hook Python, and on gateseal's own
CPython it refuses the file. Activation clones Black's mirror from GitHub and
installs Black from PyPI.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from black_policy import FORMATTED, LAZY, PYTHON, REVISION, add_black
from conftest import published
from installation import clone, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

CHOICES = '\n[choices.python]\nformatter = "black"\ntarget_version = "py315"\n'
"""The how-to's choices: Black, and Ruff checking for the syntax of Python 3.15."""

OLDER = (
    "This could be caused by running Black with an older Python version that "
    "does not support new syntax used in your source file."
)


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    """A policy repository with a release on the runtime and one with the pin."""
    origin = tmp_path_factory.mktemp("black policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "runtime", lambda data: add_black(data, python=False))
    publish(origin, "hook-python", add_black)
    return origin


def adopt(installed, origin, root, tag):
    """Adopt release tag in a new clone, pick Black, activate and commit."""
    consumer = clone(root / "origin", root / "consumer")
    env = environment(installed, origin)
    installed.cli(
        consumer,
        "configure",
        *("--capability", "hygiene", "--capability", "python"),
        *("--policy", URL, "--policy-path", "policy", "--policy-tag", tag),
        *("--compiler-wheel", installed.wheel),
        *("--compiler-sha256", installed.checksum),
        env=env,
    )
    manifest = consumer / "gateseal.toml"
    manifest.write_text(manifest.read_text() + CHOICES)
    installed.cli(consumer, "configure", env=env)
    assert installed.cli(consumer, "activate", env=env)["code"] == "prepared"
    success(["git", "add", "-A"], consumer)
    success(["git", "commit", "-m", "Format with Black"], consumer, env)
    return consumer, env


@pytest.fixture(scope="module")
def on_hook_python(installed, origin, tmp_path_factory):
    root = tmp_path_factory.mktemp("black on the hook python ü")
    return adopt(installed, origin, root, "hook-python")


@pytest.fixture(scope="module")
def on_runtime(installed, origin, tmp_path_factory):
    root = tmp_path_factory.mktemp("black on the runtime ü")
    return adopt(installed, origin, root, "runtime")


def commit(consumer, env, name, content):
    (consumer / name).write_text(content)
    success(["git", "add", name], consumer)
    result = subprocess.run(
        ["git", "commit", "-m", f"Add {name}"],
        cwd=consumer,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, result.stdout + result.stderr


def notes(result, category):
    return [d["message"] for d in result["diagnostics"] if d["category"] == category]


def test_status_and_doctor_name_the_alternative_and_the_hook_python(
    installed, on_hook_python
):
    consumer, env = on_hook_python
    for command in ("status", "doctor"):
        result = installed.cli(consumer, command, env=env)
        assert "Choice python.formatter: black." in notes(result, "policy_choice")
        assert notes(result, "hook_python") == [
            "Python-language hooks run on the policy's Hook Python, CPython "
            "3.15.2 build 20261026."
        ]
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["components"]["python"] == {"version": "3.15.2", "python": PYTHON}
    assert lock["components"]["black"]["revision"] == REVISION
    assert lock["components"]["ruff"]["hook_ids"] == ["ruff-check"]


def test_every_hook_environment_is_built_on_the_hook_python(on_hook_python):
    consumer, _ = on_hook_python
    record = json.loads((published(consumer) / "prepared.json").read_bytes())
    executable = Path(record["hook_python"]["executable"])
    runtime = Path(record["paths"]["interpreter"])
    assert executable.parents[3] == runtime.parents[3], "in the interpreter store"
    assert executable.parents[2] != runtime.parents[2], "in an entry of its own"
    assert (published(consumer) / "tools/python3.15.2").resolve() == executable
    components = set()
    for directory, prepared in record["hook_environments"].items():
        components.add(prepared["component"])
        assert Path(directory).name == "py_env-python3.15.2"
        configuration = (Path(directory) / "pyvenv.cfg").read_text()
        assert "version_info = 3.15.2.final.0" in configuration
        settings = dict(
            line.split(" = ", 1) for line in configuration.splitlines() if " = " in line
        )
        assert settings["home"] == str(executable.parent), "not the attempt's tools"
        assert settings["base-executable"] == str(executable)
        assert os.readlink(Path(directory) / "bin/python") == str(executable)
    assert components == {"pre-commit-hooks", "ruff", "black"}
    entries = {Path(directory).parents[1] for directory in record["hook_environments"]}
    assert {entry.parent for entry in entries} == {executable.parents[3]}
    assert len(record["trees"]) == 4 + len(entries)


def test_black_on_the_hook_python_formats_syntax_the_runtime_cannot_parse(
    on_hook_python,
):
    consumer, env = on_hook_python
    result, output = commit(consumer, env, "lazy.py", LAZY)
    assert result.returncode == 1, output
    assert "- hook id: black\n- files were modified by this hook" in output, output
    assert (consumer / "lazy.py").read_text() == FORMATTED
    result, output = commit(consumer, env, "lazy.py", FORMATTED)
    assert result.returncode == 0, output


def test_black_on_the_runtime_refuses_the_newer_syntax(installed, on_runtime):
    consumer, env = on_runtime
    status = installed.cli(consumer, "status", env=env)
    assert notes(status, "hook_python") == []
    result, output = commit(consumer, env, "lazy.py", LAZY)
    assert result.returncode == 1, output
    assert OLDER in output, output
    assert (consumer / "lazy.py").read_text() == LAZY


LIVE_BUILD_FIELDS = {"build", "sha256", "url"}


def assert_live_build_of_python(component: dict[str, Any]) -> None:
    """Accept whichever build upstream publishes newest, with its own digests."""
    assert list(component) == ["versions"]
    records = component["versions"]["3.15.2"]
    assert records.keys() == PYTHON.keys()
    for key, record in records.items():
        expected = {k: v for k, v in PYTHON[key].items() if k not in LIVE_BUILD_FIELDS}
        assert {
            k: v for k, v in record.items() if k not in LIVE_BUILD_FIELDS
        } == expected
        assert re.fullmatch("[0-9a-f]{64}", record["sha256"])
        assert re.fullmatch("[0-9]{8}", record["build"])
        assert f"%2B{record['build']}-" in record["url"]


@pytest.mark.network
def test_policy_add_reviews_the_hook_python_from_python_build_standalone(
    installed, tmp_path
):
    """Lock the how-to's policy, then add CPython 3.15.2, from GitHub."""
    policy = tmp_path / "policy"
    installed.cli(tmp_path, "policy", "init", str(policy))
    add_black(policy, python=False, locked=False)
    installed.cli(tmp_path, "policy", "lock", str(policy))
    added = installed.cli(tmp_path, "policy", "add", "python", "3.15.2", str(policy))
    assert added["code"] == "added"
    lock = json.loads((policy / "policy.lock").read_bytes())
    assert lock["components"]["black"] == {
        "versions": {"26.10.1": {"revision": REVISION}}
    }
    assert_live_build_of_python(lock["components"]["python"])
    assert installed.cli(tmp_path, "policy", "check", str(policy))["code"] == (
        "consistent"
    )
