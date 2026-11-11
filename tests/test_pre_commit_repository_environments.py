"""Hook environments that pre-commit prepared, attributed to locked repositories.

Each store is laid out as pre-commit lays it out: db.db names every clone by
repository and revision, and a clone holds a py_env-* environment with its
.install_state_v1 marker. uv builds each environment in milliseconds, and the
test appends the base-executable line that virtualenv, which pre-commit runs,
records. The hook executables in it are shell scripts that log each run, since
the real ones come from installing each repository from PyPI.
"""

import hashlib
import os
import shlex
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest
from gateseal.bootstrap import HookEnvironment, PreparedPaths, PreparedRecord
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_bundled_policy
from gateseal.tools import load_definition
from gateseal.tools.model import Catalog, Origin, ToolUse
from gateseal.tools.pre_commit_repository import (
    hook_environments,
    probe,
    repository_tools,
    stored_repositories,
)
from installation import success
from prepared_attempts import executable

INTERPRETER = Path(sys.executable).resolve()
HOOKS = ("trailing-whitespace-fixer", "end-of-file-fixer", "check-yaml")
RUFF_PROBES = [
    "ruff|check --config .gateseal/ruff.toml --no-cache --stdin-filename "
    "gateseal-probe.py -",
    "ruff|format --config .gateseal/ruff.toml --no-cache --stdin-filename "
    "gateseal-probe.py -",
    "ruff|--version",
]
TEAM = """\
kind = "pre-commit-repository"
stages = ["commit", "run", "ci"]
scope = "files"
removed_prefixes = []

[source]
repository = "https://github.com/team/lint"
tag = "v{version}"

[executable]
name = "lint"
version = ["--version"]
version_output = "lint {version}"

[hooks.lint-files]
name = "lint files"
probe = ["lint-files", "--help"]
output = "plain"
"""


@pytest.fixture(scope="module")
def tools():
    return load_bundled_policy().tools


def program(log, version=None, fails=False):
    """Return a hook executable that logs its name and arguments to log."""
    text = (
        "#!/bin/sh\n"
        f'printf \'%s|%s\\n\' "$(basename "$0")" "$*" >> {shlex.quote(str(log))}\n'
    )
    if version:
        text += f'[ "$1" = --version ] && echo "{version}"\n'
    return text + ("echo broken >&2\nexit 1\n" if fails else "exit 0\n")


def store(home, clones):
    """Write db.db naming each clone path by its (repository, revision)."""
    home.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(home / "db.db")
    with connection:
        connection.execute(
            "CREATE TABLE repos (repo TEXT NOT NULL, ref TEXT NOT NULL, "
            "path TEXT NOT NULL, PRIMARY KEY (repo, ref))"
        )
        connection.executemany(
            "INSERT INTO repos VALUES (?, ?, ?)",
            [(repo, ref, str(path)) for path, (repo, ref) in clones.items()],
        )
    connection.close()


def marked(clone, config=None):
    """Return clone's environment folder with its marker, and pyvenv.cfg if given."""
    env = clone / "py_env-python3"
    env.mkdir(parents=True)
    (env / ".install_state_v1").write_text(f'{{"clone": "{clone.name}"}}\n')
    if config is not None:
        (env / "pyvenv.cfg").write_text(config)
    return env


def built(clone, programs):
    """Build clone's hook environment on INTERPRETER with programs in its bin."""
    uv = shutil.which("uv")
    assert uv, "the unit layer runs under uv"
    env = clone / "py_env-python3"
    clone.mkdir(parents=True)
    success(
        [uv, "venv", "--no-config", "--no-python-downloads", "-q"]
        + ["--python", sys.executable, env],
        clone.parent,
    )
    with (env / "pyvenv.cfg").open("a") as config:
        config.write(f"base-executable = {sys.executable}\n")
    for name, text in programs.items():
        executable(env / "bin" / name, text)
    (env / ".install_state_v1").write_text(f'{{"clone": "{clone.name}"}}\n')
    return env


def logged(path):
    return path.read_text().splitlines()


def digest(env):
    return hashlib.sha256((env / ".install_state_v1").read_bytes()).hexdigest()


def prepared(tools, home, root):
    return hook_environments(
        tools, home, INTERPRETER, shutil.which("uv"), root, dict(os.environ)
    )


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


def origin(tool):
    return tool.source.repository, tool.release.revision


def test_only_hook_repositories_are_repository_tools(tools):
    assert repository_tools(tools.values()) == {
        "pre-commit-hooks": tools["pre-commit-hooks"],
        "ruff": tools["ruff"],
    }


def test_each_environment_is_attributed_probed_and_recorded(tools, tmp_path):
    root, log = tmp_path.resolve(), tmp_path.resolve() / "log"
    home = root / "home"
    hooks = built(home / "repo-a", {name: program(log) for name in HOOKS})
    ruff = built(home / "repo-b", {"ruff": program(log, "ruff 0.17.1")})
    store(
        home,
        {
            hooks.parent: origin(tools["pre-commit-hooks"]),
            ruff.parent: origin(tools["ruff"]),
        },
    )
    assert prepared(tools.values(), home, root) == {
        hooks: HookEnvironment(
            "pre-commit-hooks",
            hooks / "bin/python",
            digest(hooks),
            tuple(hooks / "bin" / name for name in sorted(HOOKS)),
        ),
        ruff: HookEnvironment(
            "ruff", ruff / "bin/python", digest(ruff), (ruff / "bin/ruff",)
        ),
    }
    assert logged(log) == [f"{name}|" for name in HOOKS] + RUFF_PROBES


def test_an_environment_records_the_executable_its_version_proof_runs(tmp_path):
    root, log = tmp_path.resolve(), tmp_path.resolve() / "log"
    definition = load_definition("lint", TEAM.encode(), Origin.TEAM)
    use = ToolUse("hygiene", tuple(definition.hooks.values()), None)
    tool = definition.source.offer(
        definition,
        use,
        Catalog("1.0.0", {"1.0.0": {}}),
        {"1.0.0": {"revision": "f" * 40}},
    )
    env = built(
        root / "home/repo",
        {"lint": program(log, "lint 1.0.0"), "lint-files": program(log)},
    )
    store(root / "home", {env.parent: (tool.source.repository, "f" * 40)})
    [recorded] = prepared([tool], root / "home", root).values()
    assert recorded.executables == (env / "bin/lint", env / "bin/lint-files")
    assert logged(log) == ["lint-files|--help", "lint|--version"]


def test_an_environment_from_no_locked_repository_is_refused(tools, tmp_path):
    home = tmp_path.resolve() / "home"
    env = marked(home / "repo")
    store(home, {env.parent: ("https://github.com/someone/hooks", "e" * 40)})
    failure = refused(prepared, tools.values(), home, tmp_path)
    assert (failure.code, failure.exit_code) == ("hook_preparation_failed", 4)
    assert str(env) in str(failure)


@pytest.mark.parametrize(
    "config,base",
    [
        (
            "home = /opt/python/bin\nbase-executable = /opt/python/bin/python3\n",
            Path("/opt/python/bin/python3").resolve(),
        ),
        ("home = /opt/python/bin\n", None),
        (None, None),
    ],
    ids=["another interpreter", "no base-executable", "no pyvenv.cfg"],
)
def test_an_environment_built_on_another_interpreter_is_refused(
    tools, tmp_path, config, base
):
    home = tmp_path.resolve() / "home"
    env = marked(home / "repo", config)
    store(home, {env.parent: origin(tools["ruff"])})
    failure = refused(prepared, tools.values(), home, tmp_path)
    assert (failure.code, failure.exit_code) == ("hook_preparation_failed", 4)
    named = (env, base, INTERPRETER)
    assert all(f"{value}" in str(failure) for value in named if value is not None)


def test_every_hook_repository_needs_an_environment(tools, tmp_path):
    failure = refused(prepared, tools.values(), tmp_path / "home", tmp_path)
    assert (failure.code, failure.exit_code) == ("hook_preparation_failed", 4)
    assert all(name in str(failure) for name in ("pre-commit-hooks", "ruff"))


def test_a_store_names_each_clone_by_its_resolved_path(tmp_path):
    home = tmp_path.resolve() / "home"
    store(home, {home / "unused" / ".." / "repo": ("https://example.org/r", "a" * 40)})
    assert stored_repositories(home) == {
        home / "repo": ("https://example.org/r", "a" * 40)
    }


def test_an_unreadable_store_fails_preparation(tmp_path):
    connection = sqlite3.connect(tmp_path / "db.db")
    with connection:
        connection.execute("CREATE TABLE clones (path TEXT)")
    connection.close()
    failure = refused(stored_repositories, tmp_path)
    assert (failure.code, failure.exit_code) == ("hook_preparation_failed", 4)
    assert "no such table: repos" in str(failure)


def record(root, folders):
    """Return an attempt's record of the hook environments in folders."""
    names = ("git", "interpreter", "compiler", "pre-commit", "bootstrap", "home")
    paths = PreparedPaths(*(root / name for name in names), root / "tools")
    hooks = {
        root / name: HookEnvironment(
            component, root / name / "bin/python", "0" * 64, ()
        )
        for name, component in folders
    }
    return PreparedRecord(
        root, root, "0" * 64, "linux-x64", paths, (), {}, None, {}, hooks, None
    )


def environments(root, hooks=None, ruff="ruff 0.17.1"):
    """Write a pre-commit-hooks and a ruff environment's programs in root.

    hooks replaces a hook program by name, and ruff is what ruff --version
    prints. Returns each environment's folder and component.
    """
    log = root / "log"
    for name in HOOKS:
        executable(root / "hooks/bin" / name, (hooks or {}).get(name) or program(log))
    executable(root / "ruff/bin/ruff", program(log, ruff))
    return [("hooks", "pre-commit-hooks"), ("ruff", "ruff")]


def test_probe_reruns_each_environments_probes_and_returns_its_versions(
    tools, tmp_path
):
    root = tmp_path.resolve()
    attempt = record(root, environments(root))
    assert probe(tools.values(), attempt, root, dict(os.environ)) == ["ruff 0.17.1"]
    assert logged(root / "log") == [f"{name}|" for name in HOOKS] + RUFF_PROBES


def test_probe_refuses_an_environment_of_a_tool_the_lock_does_not_select(
    tools, tmp_path
):
    root = tmp_path.resolve()
    attempt = record(root, environments(root))
    selected = [tools["pre-commit-hooks"]]
    failure = refused(probe, selected, attempt, root, dict(os.environ))
    assert (failure.code, failure.exit_code) == ("needs_activation", 3)
    assert failure.next_step == "gateseal activate"
    assert "ruff" in str(failure)


def test_a_failed_probe_names_its_executable(tools, tmp_path):
    root = tmp_path.resolve()
    failing = {"check-yaml": program(root / "log", fails=True)}
    attempt = record(root, environments(root, failing))
    failure = refused(probe, tools.values(), attempt, root, dict(os.environ))
    assert (failure.code, failure.exit_code) == ("tool_failed", 4)
    assert failure.next_step == "gateseal activate"
    executable = root / "hooks/bin/check-yaml"
    assert all(f"{value}" in str(failure) for value in (executable, "broken"))


def test_a_hook_environment_must_report_its_pinned_version(tools, tmp_path):
    root = tmp_path.resolve()
    attempt = record(root, environments(root, ruff="ruff 0.16.0"))
    failure = refused(probe, tools.values(), attempt, root, dict(os.environ))
    assert failure.code == "tool_failed"
    named = (root / "ruff/bin/ruff", "ruff 0.16.0", "0.17.1")
    assert all(f"{value}" in str(failure) for value in named)
