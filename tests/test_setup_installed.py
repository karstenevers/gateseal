"""Set up a fresh home with the installed gateseal, real uv, Git and pre-commit."""

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from installation import process, success

PROFILES = (
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".zshenv",
    ".config/fish",
)
"""The shell startup files where a tool could add itself to PATH."""


@dataclass(frozen=True)
class Machine:
    home: Path
    environment: dict[str, str]
    setup: dict[str, object]
    user_bin: list[str]
    """The folder of the developer's own uv tools, listed before setup."""

    @property
    def root(self) -> Path:
        return self.home / ".local" / "share" / "gateseal"

    def clone(self, origin, path):
        success(["git", "clone", "--quiet", origin, path], self.home, self.environment)
        for key, value in {
            "user.name": "gateseal test",
            "user.email": "t@e.invalid",
        }.items():
            success(["git", "config", key, value], path, self.environment)
        return path


@pytest.fixture(scope="module")
def machine(installed, tmp_path_factory):
    base = tmp_path_factory.mktemp("setup machine ü").resolve()
    home = base / "home"
    home.mkdir()
    environment = dict(
        installed.environment,
        HOME=str(home),
        GIT_CONFIG_GLOBAL=str(home / ".gitconfig"),
        PRE_COMMIT_HOME=str(base / "pre-commit home"),
        UV_PYTHON_CACHE_DIR=str(base.parent / "setup python downloads"),
    )
    environment.pop("XDG_DATA_HOME", None)
    # The developer's own pre-commit, installed with uv in uv's default folders.
    success(
        ["uv", "tool", "install", "--python", "3.14", "pre-commit==4.6.0"],
        home,
        environment,
    )
    user_bin = sorted(os.listdir(home / ".local" / "bin"))
    result = installed.cli(home, "setup", env=environment)
    return Machine(home, environment, result, user_bin)


def test_setup_leaves_the_developers_own_pre_commit_and_path(machine):
    assert machine.setup["code"] == "set_up"
    assert machine.setup["changed"] == [
        str(machine.root / "tools" / "pre-commit"),
        str(machine.root / "git-template" / "hooks" / "pre-commit"),
        "init.templateDir",
    ]
    user = success(
        [machine.home / ".local/bin/pre-commit", "--version"],
        machine.home,
        machine.environment,
    )
    assert user.stdout == "pre-commit 4.6.0\n"
    owned = success(
        [machine.root / "tools/pre-commit/bin/python", "-m", "pre_commit", "--version"],
        machine.home,
        machine.environment,
    )
    assert owned.stdout == "pre-commit 4.7.0\n"
    assert sorted(os.listdir(machine.home / ".local" / "bin")) == machine.user_bin
    assert [p for p in PROFILES if (machine.home / p).exists()] == []
    on_path = shutil.which("pre-commit", path=machine.environment["PATH"])
    assert on_path is None or not Path(on_path).is_relative_to(machine.home)


def test_a_second_setup_changes_nothing(installed, machine):
    again = installed.cli(machine.home, "setup", env=machine.environment)
    assert (again["code"], again["changed"]) == ("already_set_up", [])


def test_a_new_clone_runs_the_launcher_at_commit(machine, tmp_path):
    success(["git", "init", "--quiet", "--bare", tmp_path / "origin"], tmp_path)
    clone = machine.clone(tmp_path / "origin", tmp_path / "clone ü")
    hooks = clone / ".git" / "hooks"
    assert sorted(p.name for p in hooks.iterdir()) == ["pre-commit"]
    launcher = machine.root / "git-template" / "hooks" / "pre-commit"
    assert (hooks / "pre-commit").read_bytes() == launcher.read_bytes()
    (clone / "notes.txt").write_text("notes\n")
    success(["git", "add", "notes.txt"], clone, machine.environment)
    committed = success(
        ["git", "commit", "-m", "Add notes"], clone, machine.environment
    )
    assert "config file not found. Skipping `pre-commit`." in committed.stderr
    (clone / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: local\n  hooks:\n  - id: refuse\n    name: refuse\n"
        "    language: fail\n    entry: pre-commit ran\n    files: refused\n"
    )
    (clone / "refused").write_text("x\n")
    success(["git", "add", "-A"], clone, machine.environment)
    refused = process(["git", "commit", "-m", "Refused"], clone, machine.environment)
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "pre-commit ran" in refused.stderr


@pytest.mark.skipif(shutil.which("git-lfs") is None, reason="needs Git LFS")
def test_git_lfs_installs_and_pushes_after_setup(machine, tmp_path):
    origin = tmp_path / "origin.git"
    success(["git", "init", "--quiet", "--bare", origin], tmp_path)
    clone = machine.clone(origin, tmp_path / "clone")
    success(["git", "lfs", "install"], clone, machine.environment)
    success(["git", "lfs", "track", "*.bin"], clone, machine.environment)
    (clone / "blob.bin").write_bytes(bytes(range(256)) * 4)
    success(["git", "add", "-A"], clone, machine.environment)
    success(["git", "commit", "-m", "Add a blob"], clone, machine.environment)
    success(
        ["git", "push", "--quiet", "origin", "HEAD:main"], clone, machine.environment
    )
    pointer = success(["git", "lfs", "ls-files", "--long"], clone, machine.environment)
    oid = pointer.stdout.split()[0]
    assert (origin / "lfs" / "objects" / oid[:2] / oid[2:4] / oid).is_file()
