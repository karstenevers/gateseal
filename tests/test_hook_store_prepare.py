"""Prepare hook entries with a pre-commit whose install-hooks fakes the install.

pre-commit install-hooks clones each hook repository and installs its
packages from an index, so the pre-commit here builds a stand-in environment
from the settings file that FAKE_PRE_COMMIT names: a clone directory in its
home, recorded in db.db, whose environment links to the interpreter the
settings name and holds each probe as a script. The uv from
test_interpreter_store_acquire finds that environment's python.
"""

import dataclasses
import itertools
import json
import platform
import stat

import pytest
from gateseal import bootstrap, hook_store, interpreter_store
from gateseal.diagnostics import GatesealError
from gateseal.hook_python import HookPython
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from gateseal.tools.pre_commit_repository import repository_tools
from test_interpreter_store_acquire import fake_uv, python_program

POLICY = load_bundled_policy()
MTIME_NS = 1_700_000_000 * 10**9
FAKE_PRE_COMMIT = r"""
import json
import os
import shlex
import sqlite3
import sys
import uuid
from pathlib import Path

settings = json.loads(Path(os.environ["FAKE_PRE_COMMIT"]).read_bytes())
arguments = sys.argv[1:]
if arguments[:6] != ["-I", "-B", "-m", "pre_commit", "install-hooks", "--config"]:
    sys.exit(f"fake pre-commit cannot run {arguments}")
home = Path(os.environ["PRE_COMMIT_HOME"])
with open(settings["log"], "a") as log:
    log.write(f"{home}\n")
config = json.loads(Path(arguments[6]).read_bytes())
home.mkdir(parents=True, exist_ok=True)
database = sqlite3.connect(home / "db.db")
database.execute(
    "CREATE TABLE IF NOT EXISTS repos (repo TEXT NOT NULL, ref TEXT NOT NULL, "
    "path TEXT NOT NULL, PRIMARY KEY (repo, ref))"
)
for repository in config["repos"]:
    source = (repository["repo"], repository["rev"])
    query = "SELECT path FROM repos WHERE repo = ? AND ref = ?"
    if database.execute(query, source).fetchone():
        continue
    for suffix in settings["clones"]:
        clone = Path(settings.get("clone_dir", home)) / ("repo" + uuid.uuid4().hex[:8])
        environment = clone / "py_env-python3"
        (environment / "bin").mkdir(parents=True)
        (environment / "bin" / "python").symlink_to(settings["base"])
        (environment / "pyvenv.cfg").write_text(
            f"base-executable = {settings['base']}\n"
        )
        for name, output in settings["executables"].items():
            program = environment / "bin" / name
            program.write_text(f"#!/bin/sh\necho {shlex.quote(output)}\n")
            program.chmod(0o755)
        marker = environment / ".install_state_v1"
        marker.write_text('{"additional_dependencies": []}')
        os.utime(marker, ns=(settings["mtime"], settings["mtime"]))
        row = (source[0] + suffix, source[1], str(clone))
        database.execute("INSERT INTO repos VALUES (?, ?, ?)", row)
database.commit()
database.close()
"""


def selection(capabilities, python=None):
    manifest = parse_manifest(render(capabilities, BundledSource(POLICY.release)))
    return dataclasses.replace(resolve(manifest, POLICY), python=python)


def interpreter(tmp_path, name):
    root = tmp_path.resolve() / name
    (root / "bin").mkdir(parents=True)
    python = root / "bin" / "python3"
    python.write_text("")
    return interpreter_store.Interpreter(root, python, bootstrap.TreeDigests("", ""))


def attempt(tmp_path, digit):
    path = tmp_path.resolve() / "gateseal" / ("attempt-" + digit * 32)
    path.mkdir(parents=True)
    return path


@pytest.fixture
def store(monkeypatch, tmp_path):
    store = tmp_path.resolve() / "store"
    monkeypatch.setenv("GATESEAL_INTERPRETER_STORE", str(store))
    return store


class Preparation:
    """Run hook_store.prepare in a worktree with the fake pre-commit and uv."""

    def __init__(self, tmp_path, chosen, interpreters, **settings):
        self.chosen, self.interpreters = chosen, interpreters
        self.root = tmp_path.resolve() / "worktree"
        self.root.mkdir()
        tools = repository_tools(chosen.tools).values()
        name = None if chosen.python is None else chosen.python.name
        config = {"repos": [tool.entry(None, name) for tool in tools]}
        (self.root / bootstrap.CONFIG).write_text(json.dumps(config))
        fakes = tmp_path / "fakes"
        self.uv = fake_uv(fakes)
        self.pre_commit = python_program(fakes / "python", FAKE_PRE_COMMIT)
        self.log = fakes / "pre-commit.log"
        executables = {hook.probe[0]: "" for tool in tools for hook in tool.use.hooks}
        executables.update(
            {
                tool.definition.executable.name: f"ruff {tool.version}"
                for tool in tools
                if tool.definition.executable is not None
            }
        )
        self.settings = fakes / "pre-commit.json"
        document = {
            "log": str(self.log),
            "base": str(interpreters[-1].executable),
            "executables": executables,
            "mtime": MTIME_NS,
            "clones": [""],
            **settings,
        }
        self.settings.write_text(json.dumps(document))
        self.uv_env = {"FAKE_UV_LOG": str(fakes / "uv.log")}

    def __call__(self, attempt):
        paths = bootstrap.PreparedPaths(
            git=attempt / "git",
            interpreter=self.interpreters[0].executable,
            compiler_python=attempt / "compiler" / "bin" / "python",
            pre_commit_python=self.pre_commit,
            bootstrap=attempt / "bootstrap.py",
            pre_commit_home=attempt / "pre-commit-home",
            tools=attempt / "tools",
        )
        hook_env = {
            "PRE_COMMIT_HOME": str(paths.pre_commit_home),
            "FAKE_PRE_COMMIT": str(self.settings),
        }
        return hook_store.prepare(
            self.chosen,
            attempt,
            paths,
            self.interpreters,
            str(self.uv),
            self.root,
            self.uv_env,
            hook_env,
        )

    def homes(self):
        return self.log.read_text().splitlines()


def test_attempts_share_one_sealed_entry_per_hook_repository(store, tmp_path):
    chosen = selection(("hygiene", "python"), HookPython("3.15.3", {}))
    interpreters = (interpreter(tmp_path, "runtime"), interpreter(tmp_path, "hooks"))
    prepare = Preparation(tmp_path, chosen, interpreters)
    first, second = attempt(tmp_path, "1"), attempt(tmp_path, "2")
    (second / "pre-commit-home").mkdir(mode=0o700)
    entries, environments = prepare(first)
    assert prepare(second) == (entries, environments)
    homes = [first / "pre-commit-home", second / "pre-commit-home"]
    assert prepare.homes() == [str(path) for path in (*entries, *homes)]
    assert {entry.parent for entry in entries} == {store}
    assert sorted(environment.component for environment in environments.values()) == [
        "pre-commit-hooks",
        "ruff",
    ]
    for path in environments:
        assert sum(path.is_relative_to(entry) for entry in entries) == 1
        marker = (path / ".install_state_v1").stat()
        assert (stat.S_IMODE(marker.st_mode), marker.st_mtime_ns) == (0o444, MTIME_NS)
    assert stat.S_IMODE(homes[0].stat().st_mode) == 0o700
    assert all(interpreter_store.linked(path, environments) for path in (first, second))


@pytest.mark.parametrize("clones", [[], ["", ":extra"]], ids=["no clone", "two"])
def test_a_hook_entry_without_exactly_one_clone_is_refused(store, tmp_path, clones):
    prepare = Preparation(
        tmp_path,
        selection(("hygiene",)),
        (interpreter(tmp_path, "runtime"),),
        clones=clones,
    )
    with pytest.raises(GatesealError) as refused:
        prepare(attempt(tmp_path, "1"))
    assert (refused.value.code, refused.value.exit_code) == (
        "hook_preparation_failed",
        4,
    )
    assert f"{len(clones)} clones" in str(refused.value)


def test_an_environment_built_outside_the_store_is_refused(store, tmp_path):
    elsewhere = tmp_path.resolve() / "elsewhere"
    prepare = Preparation(
        tmp_path,
        selection(("hygiene",)),
        (interpreter(tmp_path, "runtime"),),
        clone_dir=str(elsewhere),
    )
    with pytest.raises(GatesealError) as refused:
        prepare(attempt(tmp_path, "1"))
    assert (refused.value.code, refused.value.exit_code) == (
        "hook_preparation_failed",
        4,
    )
    assert str(elsewhere) in str(refused.value)


def test_hook_keys_that_never_change_an_environment_share_an_entry(tmp_path):
    (tool,) = repository_tools(selection(("hygiene",)).tools).values()
    renamed = tool.using(
        tuple(
            dataclasses.replace(
                hook, name="renamed " + hook.id, args=("--fix=no",), types_or=("text",)
            )
            for hook in tool.use.hooks
        )
    )
    python = tmp_path / "python3.14"
    assert hook_store.recipe(renamed, None, python) == hook_store.recipe(
        tool, None, python
    )


def test_anything_that_changes_a_hook_environment_names_another_entry(
    monkeypatch, tmp_path
):
    (tool,) = repository_tools(selection(("hygiene",)).tools).values()
    python = tmp_path / "python3.14"
    recipes = [
        hook_store.recipe(tool, None, python),
        hook_store.recipe(tool, "python3.15.3", python),
        hook_store.recipe(tool, None, tmp_path / "python3.15"),
        hook_store.recipe(tool.select("5.0.0"), None, python),
        hook_store.recipe(tool.using(tool.use.hooks[:1]), None, python),
    ]
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    recipes.append(hook_store.recipe(tool, None, python))
    for one, other in itertools.combinations(recipes, 2):
        assert one != other
