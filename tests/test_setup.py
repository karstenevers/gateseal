"""Set up the owned pre-commit and its launcher, with a uv that builds no wheel.

The stand-in uv answers `uv tool install` with a virtual environment that
reads the test interpreter's pre-commit through a .pth file, so the launcher,
the template and the clones are pre-commit's own, offline and on every Python.
"""

import contextlib
import io
import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pre_commit
import pytest
from gateseal import setup
from gateseal.cli import main
from installation import process, success

PYTHON = "{}.{}".format(*sys.version_info)
GIT = shutil.which("git")
UV = shutil.which("uv")
SITE = Path(pre_commit.__file__).parents[1]
MISE = "2026.10.25 linux-x64 (2026-10-25)"


@dataclass(frozen=True)
class Machine:
    home: Path
    shims: Path
    installs: Path
    """The stand-in uv appends each `uv tool install` call here."""

    @property
    def root(self) -> Path:
        return (self.home / ".local" / "share" / "gateseal").resolve()

    @property
    def launcher(self) -> Path:
        return self.root / "git-template" / "hooks" / "pre-commit"

    def calls(self) -> list[list[str]]:
        if not self.installs.exists():
            return []
        return [json.loads(line) for line in self.installs.read_text().splitlines()]

    def shim(self, name, script):
        path = self.shims / name
        # Writing through a symlink to the real tool would replace that tool.
        path.unlink(missing_ok=True)
        path.write_text("#!/bin/sh\n" + script)
        path.chmod(0o755)


def gateseal(*arguments):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--json", *map(str, arguments)])
    return code, json.loads(output.getvalue())


def message(result):
    return result["diagnostics"][0]["message"]


def notes(result):
    return [d for d in result["diagnostics"] if d["severity"] != "info"]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    home = tmp_path / "home ü"
    home.mkdir()
    shims = tmp_path / "shims"
    shims.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / ".gitconfig"))
    monkeypatch.setenv("PRE_COMMIT_HOME", str(tmp_path / "pre-commit home"))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("GIT_TEMPLATE_DIR", raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(shims), "/usr/bin", "/bin"]))
    monkeypatch.setattr(setup, "PYTHON", PYTHON)
    # A refresh that loses its --repo then fails here instead of finding the
    # repository that runs the tests.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    result = Machine(home, shims, tmp_path / "uv installs")
    (shims / "git").symlink_to(GIT)
    log = (
        f"{shlex.quote(sys.executable)} -c 'import json, os, sys; "
        'print(json.dumps(sys.argv[1:] + [os.environ["UV_TOOL_DIR"], '
        'os.environ["UV_TOOL_BIN_DIR"]]))\' "$@" '
        f">> {shlex.quote(str(result.installs))}\n"
    )
    result.shim(
        "uv",
        'if [ "$1 $2" = "tool install" ]; then\n'
        f"  {log}"
        '  venv="$UV_TOOL_DIR/pre-commit"\n'
        f'  {shlex.quote(sys.executable)} -m venv --clear --without-pip "$venv"\n'
        '  exec "$venv/bin/python" -c "import site, sys; '
        "open(site.getsitepackages()[0] + '/test.pth', 'w').write(sys.argv[1])\" "
        f"{shlex.quote(str(SITE))}\n"
        "fi\n"
        f'exec {shlex.quote(UV)} "$@"\n',
    )
    result.shim("mise", f'echo "{MISE}"\n')
    return result


def repository(path, *options):
    success(["git", "init", "--quiet", "--initial-branch=main", *options, path], "/")
    success(["git", "config", "user.name", "gateseal test"], path)
    success(["git", "config", "user.email", "gateseal@example.invalid"], path)
    return path


def commit(root, env=None):
    (root / "file.txt").write_text("content\n")
    success(["git", "add", "file.txt"], root)
    return process(["git", "commit", "--quiet", "-m", "Add a file"], root, env)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (None, "home/.local/share/gateseal"),
        ("", "home/.local/share/gateseal"),
        ("relative/data", "home/.local/share/gateseal"),
        ("{tmp}/data", "data/gateseal"),
    ],
)
def test_data_root_follows_an_absolute_xdg_data_home(
    tmp_path, monkeypatch, data, expected
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    if data is None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_DATA_HOME", data.format(tmp=tmp_path))
    assert setup.data_root() == (tmp_path / expected).resolve()


def test_setup_installs_pre_commit_and_the_launcher_for_new_clones(machine, tmp_path):
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (0, "set_up", "changed")
    tools = machine.root / "tools"
    assert result["changed"] == [
        str(tools / "pre-commit"),
        str(machine.launcher),
        "init.templateDir",
    ]
    version = setup.load_runtime().pre_commit
    assert machine.calls() == [
        ["tool", "install", "--reinstall", "--python", PYTHON]
        + [f"pre-commit=={version}", str(tools), str(machine.root / "bin")]
    ]
    assert message(result) == (
        f"pre-commit {version} and its commit launcher are set up. "
        "Every new clone runs pre-commit at git commit."
    )
    assert result["next_steps"] == [
        "Run gateseal setup --refresh in each clone you made before."
    ]
    assert notes(result) == []
    configured = success(["git", "config", "--global", "init.templateDir"], "/")
    assert configured.stdout == f"{machine.root / 'git-template'}\n"
    launcher = machine.launcher.read_text()
    assert f"INSTALL_PYTHON={shlex.quote(str(tools / 'pre-commit/bin/python'))}\n" in (
        launcher
    )
    assert "--hook-type=pre-commit --skip-on-missing-config)" in launcher
    assert machine.launcher.stat().st_mode & 0o777 == 0o755
    assert sorted(p.name for p in machine.launcher.parent.iterdir()) == ["pre-commit"]
    clone = repository(tmp_path / "new clone")
    assert sorted(p.name for p in (clone / ".git" / "hooks").iterdir()) == [
        "pre-commit"
    ]
    assert (clone / ".git/hooks/pre-commit").read_text() == launcher
    committed = commit(clone)
    assert committed.returncode == 0, committed.stdout + committed.stderr
    assert "config file not found. Skipping `pre-commit`." in committed.stderr


def test_a_second_setup_changes_nothing(machine):
    gateseal("setup")
    config = (machine.home / ".gitconfig").read_bytes()
    written = machine.launcher.stat().st_mtime_ns
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (0, "already_set_up", "ok")
    assert result["changed"] == []
    assert message(result).endswith(
        "commit launcher were already set up. Every new clone runs pre-commit at "
        "git commit. Nothing changed."
    )
    assert len(machine.calls()) == 1
    assert machine.launcher.stat().st_mtime_ns == written
    assert (machine.home / ".gitconfig").read_bytes() == config


def test_setup_restores_a_changed_launcher(machine):
    gateseal("setup")
    launcher = machine.launcher.read_bytes()
    machine.launcher.write_text("#!/bin/sh\nexit 0\n")
    code, result = gateseal("setup")
    assert (code, result["changed"]) == (0, [str(machine.launcher)])
    assert machine.launcher.read_bytes() == launcher


def test_setup_restores_a_launcher_without_execute_permission(machine):
    gateseal("setup")
    launcher = machine.launcher.read_bytes()
    machine.launcher.chmod(0o644)
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (0, "set_up", "changed")
    assert result["changed"] == [str(machine.launcher)]
    assert machine.launcher.read_bytes() == launcher
    assert machine.launcher.stat().st_mode & 0o777 == 0o755
    assert len(machine.calls()) == 1


@pytest.mark.parametrize("named", ["python", "pre-commit"])
def test_setup_reinstalls_pre_commit_on_another_named_version(
    machine, monkeypatch, named
):
    gateseal("setup")
    runtime = setup.load_runtime()
    if named == "python":
        # A prefix of the running version without its dot, such as 3.1 of 3.14.
        monkeypatch.setattr(setup, "PYTHON", PYTHON[:3])
    else:
        monkeypatch.setattr(
            setup, "load_runtime", lambda: replace(runtime, pre_commit="9.9.9")
        )
    code, result = gateseal("setup")
    assert (code, result["changed"][0]) == (0, str(machine.root / "tools/pre-commit"))
    assert machine.calls()[1][4:6] == [
        setup.PYTHON,
        f"pre-commit=={setup.load_runtime().pre_commit}",
    ]


def test_setup_reinstalls_a_broken_pre_commit(machine):
    gateseal("setup")
    python = machine.root / "tools/pre-commit/bin/python"
    python.unlink()
    python.symlink_to(machine.home / "removed python")
    code, result = gateseal("setup")
    assert (code, result["code"]) == (0, "set_up")
    assert len(machine.calls()) == 2


@pytest.mark.parametrize("environment_template", [False, True])
def test_setup_refuses_another_template_folder_and_changes_nothing(
    machine, monkeypatch, environment_template
):
    if environment_template:
        monkeypatch.setenv("GIT_TEMPLATE_DIR", str(machine.root / "git-template"))
    other = machine.home / "my templates"
    success(["git", "config", "--global", "init.templateDir", other], "/")
    config_file = machine.home / ".gitconfig"
    config = config_file.read_bytes()
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (
        2,
        "template_conflict",
        "failed",
    )
    assert message(result) == (
        f"Git's init.templateDir in {config_file} already names {other}, "
        "so gateseal setup changed nothing."
    )
    assert result["next_steps"] == [
        f"Keep what you need from {other}, run git config --file "
        f"{shlex.quote(str(config_file))} --unset init.templateDir, then run "
        "gateseal setup again."
    ]
    assert not machine.root.exists()
    assert machine.calls() == []
    assert (machine.home / ".gitconfig").read_bytes() == config


@pytest.mark.parametrize(
    "folder",
    [
        "{home}/my templates",
        ".local/share/gateseal/git-template",
        "~/.local/share/gateseal/git-template",
        "",
    ],
)
@pytest.mark.parametrize("existing_config", [False, True])
def test_setup_refuses_another_environment_template_and_changes_nothing(
    machine, monkeypatch, folder, existing_config
):
    folder = folder.format(home=machine.home)
    monkeypatch.chdir(machine.home)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", folder)
    if existing_config:
        success(
            ["git", "config", "--global", "init.templateDir", "other templates"], "/"
        )
    config = machine.home / ".gitconfig"
    before = config.read_bytes() if config.exists() else None
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (
        2,
        "template_conflict",
        "failed",
    )
    assert message(result) == (
        f"GIT_TEMPLATE_DIR is set to {shlex.quote(folder)}, so Git ignores "
        "init.templateDir and gateseal setup changed nothing."
    )
    assert result["next_steps"] == [
        "Unset GIT_TEMPLATE_DIR where your shell sets it, then run gateseal "
        "setup again."
    ]
    assert result["changed"] == []
    assert not machine.root.exists()
    assert machine.calls() == []
    assert (config.read_bytes() if config.exists() else None) == before


@pytest.mark.parametrize("folder", ["template", "symlink"])
def test_setup_accepts_its_own_environment_template(machine, monkeypatch, folder):
    template = machine.root / "git-template"
    named = template
    if folder == "symlink":
        named = machine.home / "templates"
        named.symlink_to(template, target_is_directory=True)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(named))
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (0, "set_up", "changed")
    assert "init.templateDir" in result["changed"]
    configured = success(["git", "config", "--global", "init.templateDir"], "/")
    assert configured.stdout == f"{template}\n"
    assert machine.launcher.is_file()
    assert len(machine.calls()) == 1


def test_setup_refuses_a_relative_template_folder_and_changes_nothing(machine):
    folder = ".local/share/gateseal/git-template"
    success(["git", "config", "--global", "init.templateDir", folder], "/")
    config = (machine.home / ".gitconfig").read_bytes()
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (
        2,
        "template_conflict",
        "failed",
    )
    assert result["changed"] == []
    assert not machine.root.exists()
    assert machine.calls() == []
    assert (machine.home / ".gitconfig").read_bytes() == config


@pytest.mark.parametrize("scope", ["system", "environment"])
def test_setup_refuses_an_effective_template_folder_and_changes_nothing(
    machine, tmp_path, monkeypatch, scope
):
    other = machine.home / "my templates"
    system = tmp_path / "system config"
    if scope == "system":
        monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
        monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
        success(["git", "config", "--system", "init.templateDir", other], "/")
    else:
        monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
        monkeypatch.setenv("GIT_CONFIG_KEY_0", "init.templateDir")
        monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(other))
    config = machine.home / ".gitconfig"
    before = config.read_bytes() if config.exists() else None
    system_before = system.read_bytes() if system.exists() else None
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (
        2,
        "template_conflict",
        "failed",
    )
    assert result["changed"] == []
    source = f" in {system}" if scope == "system" else ""
    assert message(result) == (
        f"Git's init.templateDir{source} already names {other}, so gateseal setup "
        "changed nothing."
    )
    if scope == "system":
        assert result["next_steps"] == [
            f"Keep what you need from {other}, run git config --file "
            f"{shlex.quote(str(system))} --unset init.templateDir, then run "
            "gateseal setup again."
        ]
    else:
        assert result["next_steps"] == [
            "Remove the init.templateDir setting where it is set, then run "
            "gateseal setup again."
        ]
    assert not machine.root.exists()
    assert machine.calls() == []
    assert (config.read_bytes() if config.exists() else None) == before
    assert (system.read_bytes() if system.exists() else None) == system_before


def test_setup_accepts_its_own_template_folder_from_system_config(
    machine, tmp_path, monkeypatch
):
    system = tmp_path / "system config"
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    template = machine.root / "git-template"
    success(["git", "config", "--system", "init.templateDir", template], "/")
    before = system.read_bytes()
    code, result = gateseal("setup")
    assert (code, result["code"]) == (0, "set_up")
    assert "init.templateDir" not in result["changed"]
    configured = process(
        ["git", "config", "--global", "--get", "init.templateDir"], "/"
    )
    assert (configured.returncode, configured.stdout) == (1, "")
    assert system.read_bytes() == before


def test_setup_recovers_from_an_included_template_folder(machine):
    other = machine.home / "my templates"
    included = machine.home / "included config"
    success(["git", "config", "--file", included, "init.templateDir", other], "/")
    success(["git", "config", "--global", "include.path", included], "/")
    config = (machine.home / ".gitconfig").read_bytes()
    included_config = included.read_bytes()
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (
        2,
        "template_conflict",
        "failed",
    )
    assert message(result) == (
        f"Git's init.templateDir in {included} already names {other}, "
        "so gateseal setup changed nothing."
    )
    assert result["next_steps"] == [
        f"Keep what you need from {other}, run git config --file "
        f"{shlex.quote(str(included))} --unset init.templateDir, then run "
        "gateseal setup again."
    ]
    assert not machine.root.exists()
    assert machine.calls() == []
    assert (machine.home / ".gitconfig").read_bytes() == config
    assert included.read_bytes() == included_config
    command = result["next_steps"][0].split(", run ", 1)[1].split(", then run ", 1)[0]
    success(shlex.split(command), "/")
    code, result = gateseal("setup")
    assert (code, result["code"]) == (0, "set_up")


@pytest.mark.parametrize(
    ("scope", "folder"),
    [
        ("global", "my hooks"),
        ("system", "my hooks"),
        ("environment", "my hooks"),
        ("global", ""),
    ],
)
def test_setup_refuses_a_custom_hooks_path_and_changes_nothing(
    machine, monkeypatch, scope, folder
):
    other = machine.home / folder if folder else ""
    system = machine.home / "system config"
    if scope == "environment":
        count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
        monkeypatch.setenv("GIT_CONFIG_COUNT", str(count + 1))
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{count}", "core.hooksPath")
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{count}", str(other))
    else:
        if scope == "system":
            monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
            monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
        success(["git", "config", f"--{scope}", "core.hooksPath", other], "/")
    config = machine.home / ".gitconfig"
    before = config.read_bytes() if config.exists() else None
    system_before = system.read_bytes() if system.exists() else None
    code, result = gateseal("setup")
    assert (code, result["code"], result["status"]) == (
        2,
        "custom_hooks_path",
        "failed",
    )
    assert result["changed"] == []
    assert message(result) == (
        f"Git's core.hooksPath names {other}, so Git would skip the commit launcher."
    )
    assert result["next_steps"] == [
        "Remove the core.hooksPath setting, then run gateseal setup again."
    ]
    assert not machine.root.exists()
    assert machine.calls() == []
    assert (config.read_bytes() if config.exists() else None) == before
    assert (system.read_bytes() if system.exists() else None) == system_before


def test_setup_ignores_a_local_hooks_path_in_the_home_directory(machine, monkeypatch):
    repository(machine.home, "--template=")
    success(["git", "config", "core.hooksPath", "elsewhere"], machine.home)
    monkeypatch.chdir(machine.home)
    code, result = gateseal("setup")
    assert (code, result["code"]) == (0, "set_up")


@pytest.mark.parametrize("status", [2, 3, 128])
def test_setup_stops_when_git_cannot_read_core_hooks_path(machine, status):
    machine.shim(
        "git",
        'case "$*" in "config --includes "*core.hooksPath)\n'
        f'  echo "cannot read"; exit {status} ;;\n'
        "esac\n"
        f'exec {shlex.quote(GIT)} "$@"\n',
    )
    code, result = gateseal("setup")
    assert (code, result["code"]) == (4, "tool_failed")
    assert message(result) == "cannot read\n"
    assert not machine.root.exists()
    assert machine.calls() == []


def test_setup_accepts_its_own_template_folder_written_with_a_tilde(machine):
    folder = "~/.local/share/gateseal/git-template"
    success(["git", "config", "--global", "init.templateDir", folder], "/")
    code, result = gateseal("setup")
    assert (code, result["code"]) == (0, "set_up")
    assert "init.templateDir" not in result["changed"]
    configured = success(["git", "config", "--global", "init.templateDir"], "/")
    assert configured.stdout == folder + "\n"


@pytest.mark.parametrize("status", [2, 3, 128])
def test_setup_stops_when_git_cannot_read_init_template_dir(machine, status):
    machine.shim(
        "git",
        'case "$*" in "config --includes "*init.templateDir)\n'
        f'  echo "cannot read"; exit {status} ;;\n'
        "esac\n"
        f'exec {shlex.quote(GIT)} "$@"\n',
    )
    code, result = gateseal("setup")
    assert (code, result["code"]) == (4, "tool_failed")
    assert message(result) == "cannot read\n"
    assert result["next_steps"] == [
        "Fix what Git reports, then run gateseal setup again."
    ]
    assert not machine.root.exists()
    assert machine.calls() == []


def test_setup_reports_a_failed_install(machine):
    machine.shim(
        "uv",
        'if [ "$1 $2" = "tool install" ]; then echo "No network"; exit 2; fi\n'
        f'exec {shlex.quote(UV)} "$@"\n',
    )
    code, result = gateseal("setup")
    assert (code, result["code"]) == (4, "tool_failed")
    assert message(result) == "No network\n"
    assert result["next_steps"] == [
        "Fix what uv reports, then run gateseal setup again."
    ]
    assert not (machine.root / "git-template").exists()


@pytest.mark.parametrize(
    ("missing", "name", "minimum"),
    [("git", "Git", "2.54.0"), ("uv", "uv", "0.13.5")],
)
def test_setup_names_the_minimum_version_of_a_missing_tool(
    machine, monkeypatch, missing, name, minimum
):
    (machine.shims / missing).unlink()
    # The system folders can hold another git, such as macOS's older one.
    monkeypatch.setenv("PATH", str(machine.shims))
    code, result = gateseal("setup")
    assert (code, result["code"]) == (4, "missing_prerequisite")
    assert message(result) == (
        f"gateseal setup needs {name} {minimum} or later, and none is on PATH."
    )
    assert result["next_steps"] == [
        f"Install {name} {minimum} or later, then run gateseal setup again."
    ]
    assert not machine.root.exists()


def test_setup_refuses_an_old_git_or_uv(machine):
    machine.shim(
        "git",
        'if [ "$1" = --version ]; then echo "git version 2.53.9"; exit 0; fi\n'
        f'exec {shlex.quote(GIT)} "$@"\n',
    )
    code, result = gateseal("setup")
    assert (code, result["code"]) == (4, "git_version_mismatch")
    assert "gateseal requires Git 2.54.0 or later." in message(result)
    (machine.shims / "git").unlink()
    (machine.shims / "git").symlink_to(GIT)
    machine.shim("uv", 'echo "uv 0.13.4"\n')
    code, result = gateseal("setup")
    assert (code, result["code"]) == (4, "uv_version_mismatch")
    assert message(result).endswith("requires uv 0.13.5 or later.")
    assert not machine.root.exists()


@pytest.mark.parametrize(
    ("mise", "category", "found"),
    [
        (None, "missing_prerequisite", "none is"),
        ('echo "2026.10.18 linux-x64 (2026-10-18)"\n', "mise_version_mismatch", "an"),
    ],
)
def test_setup_warns_about_a_missing_or_old_mise(machine, mise, category, found):
    (machine.shims / "mise").unlink()
    if mise:
        machine.shim("mise", mise)
    code, result = gateseal("setup")
    assert (code, result["code"]) == (0, "set_up")
    expected = "none is" if found == "none is" else "an older one is"
    assert notes(result) == [
        {
            "severity": "warning",
            "category": category,
            "message": "A Policy with a release binary, such as Betterleaks, needs "
            f"mise 2026.10.19 or later, and {expected} on PATH.",
        }
    ]


def test_refresh_installs_the_launcher_in_an_existing_clone(machine, tmp_path):
    clone = repository(tmp_path / "old clone", "--template=")
    hook = clone / ".git" / "hooks" / "pre-commit"
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["code"]) == (0, "set_up")
    assert result["changed"][-1] == str(hook)
    assert message(result).endswith(f" So does the clone at {clone}.")
    assert result["next_steps"] == []
    assert hook.read_bytes() == machine.launcher.read_bytes()
    assert sorted(p.name for p in hook.parent.iterdir()) == ["pre-commit"]
    assert notes(result) == []
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["code"], result["changed"]) == (0, "already_set_up", [])
    assert message(result).endswith(f"So does the clone at {clone}. Nothing changed.")


def test_refresh_restores_a_hook_without_execute_permission(machine, tmp_path):
    gateseal("setup")
    clone = repository(tmp_path / "clone")
    hook = clone / ".git" / "hooks" / "pre-commit"
    launcher = hook.read_bytes()
    hook.chmod(0o644)
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["code"], result["status"]) == (0, "set_up", "changed")
    assert result["changed"] == [str(hook)]
    assert hook.read_bytes() == launcher
    assert hook.stat().st_mode & 0o777 == 0o755
    assert sorted(p.name for p in hook.parent.iterdir()) == ["pre-commit"]
    assert len(machine.calls()) == 1


def test_refresh_keeps_another_tools_hook_and_runs_both(machine, tmp_path):
    clone = repository(tmp_path / "clone with a hook", "--template=")
    hook = clone / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir()
    hook.write_text(f'#!/bin/sh\necho ran > "{tmp_path}/other tool ran"\n')
    hook.chmod(0o755)
    gateseal("setup")
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["changed"]) == (0, [str(hook)])
    legacy = hook.with_name("pre-commit.legacy")
    assert result["diagnostics"][1] == {
        "severity": "info",
        "category": "legacy_hook",
        "message": f"The commit launcher in {clone} runs the earlier hook, which "
        f"pre-commit keeps as {legacy}.",
    }
    (clone / ".pre-commit-config.yaml").write_text(
        "repos:\n- repo: local\n  hooks:\n  - id: mine\n    name: mine\n"
        "    language: unsupported\n    pass_filenames: false\n"
        f"    entry: sh -c 'echo ran > \"{tmp_path}/pre-commit ran\"'\n"
    )
    success(["git", "add", ".pre-commit-config.yaml"], clone)
    committed = commit(clone)
    assert committed.returncode == 0, committed.stdout + committed.stderr
    assert (tmp_path / "other tool ran").read_text() == "ran\n"
    assert (tmp_path / "pre-commit ran").read_text() == "ran\n"


def test_refresh_outside_a_repository_changes_nothing(machine, tmp_path):
    folder = tmp_path / "not a clone"
    folder.mkdir()
    code, result = gateseal("--repo", folder, "setup", "--refresh")
    assert (code, result["code"]) == (2, "not_a_repository")
    assert not machine.root.exists()


def test_refresh_refuses_a_custom_hooks_path_and_keeps_the_hook(machine, tmp_path):
    clone = repository(tmp_path / "clone", "--template=")
    hook = clone / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir()
    hook.write_bytes(b"#!/bin/sh\n")
    success(["git", "config", "core.hooksPath", "elsewhere"], clone)
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["code"]) == (2, "custom_hooks_path")
    assert sorted(p.name for p in hook.parent.iterdir()) == ["pre-commit"]
    assert hook.read_bytes() == b"#!/bin/sh\n"
    assert "core.hooksPath" in message(result)
    assert result["next_steps"] == [
        "Remove the core.hooksPath setting, then run gateseal setup again."
    ]


def test_refresh_refuses_a_custom_hooks_path_even_with_the_launcher(machine, tmp_path):
    gateseal("setup")
    clone = repository(tmp_path / "clone")
    hook = clone / ".git" / "hooks" / "pre-commit"
    launcher = hook.read_bytes()
    written = hook.stat().st_mtime_ns
    success(["git", "config", "core.hooksPath", "elsewhere"], clone)
    config = (clone / ".git" / "config").read_bytes()
    global_config = (machine.home / ".gitconfig").read_bytes()
    code, result = gateseal("--repo", clone, "setup", "--refresh")
    assert (code, result["code"], result["status"]) == (
        2,
        "custom_hooks_path",
        "failed",
    )
    assert hook.read_bytes() == launcher
    assert hook.stat().st_mtime_ns == written
    assert sorted(p.name for p in hook.parent.iterdir()) == ["pre-commit"]
    assert (clone / ".git" / "config").read_bytes() == config
    assert (machine.home / ".gitconfig").read_bytes() == global_config
    assert len(machine.calls()) == 1


def test_an_invalid_setup_invocation_names_setup():
    code, result = gateseal("setup", "--unknown")
    assert (code, result["command"], result["code"]) == (
        2,
        "setup",
        "invalid_invocation",
    )
