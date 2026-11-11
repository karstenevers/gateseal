"""Preparing a release binary: mise's version check, the install, and the probes.

The unit layer has no mise, and a real one would download Betterleaks from
GitHub, so mise here is a shell script that logs each command and installs a
small tar.gz built in tmp_path where mise installs an archive. The archive
holds a shell script in place of the Betterleaks binary, which logs each run.
"""

import io
import json
import os
import shlex
import tarfile
from pathlib import Path

import pytest
from gateseal.bootstrap import PreparedPaths, PreparedRecord, PreparedTool
from gateseal.compiler_release import TARGETS, Target
from gateseal.diagnostics import GatesealError
from gateseal.policy import bundled_root, load_bundled_policy
from gateseal.tools import release_binary
from gateseal.tools.release_binary import Mise, NativeTool
from prepared_attempts import executable as script

LINUX = TARGETS["Linux", "x86_64"]
INSTALLED = "installs/http-betterleaks/1.9.0"
"""Where mise installs Betterleaks 1.9.0 in its data directory."""

PROBE = "config check --config .gateseal/betterleaks.toml"


@pytest.fixture(scope="module")
def tools():
    return load_bundled_policy().tools


@pytest.fixture(scope="module")
def betterleaks(tools):
    return tools["betterleaks"]


def spec():
    """Return the archive spec of Betterleaks 1.9.0 that mise installs on Linux."""
    lock = json.loads((bundled_root() / "policy.lock").read_bytes())
    archive = lock["components"]["betterleaks"]["versions"]["1.9.0"]["platforms"]
    url, sha = archive["linux-x64"]["url"], archive["linux-x64"]["sha256"]
    return f"http:betterleaks[url={url},checksum=sha256:{sha}]@1.9.0"


def prepared_paths(attempt):
    names = ("git", "interpreter", "compiler", "pre-commit", "bootstrap", "home")
    return PreparedPaths(*(attempt / name for name in names), attempt / "tools")


def logged(path):
    return path.read_text().splitlines() if path.exists() else []


def binary(log, version="1.9.0"):
    """Return a Betterleaks stand-in that logs its directory and arguments."""
    return (
        "#!/bin/sh\n"
        f'printf "%s|%s\\n" "$(pwd)" "$*" >> {shlex.quote(str(log))}\n'
        f'[ "$1" = version ] && echo {version}\nexit 0\n'
    )


def archive(path, members):
    """Write a tar.gz of members: name to (mode, text), or to a link target."""
    with tarfile.open(path, "w:gz") as tar:
        for name, member in members.items():
            info = tarfile.TarInfo(name)
            if isinstance(member, str):
                info.type, info.linkname = tarfile.SYMTYPE, member
                tar.addfile(info)
                continue
            mode, text = member
            data = text.encode()
            info.mode, info.size = mode, len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


class Machine:
    """A fake mise on PATH, the archive it installs, and an attempt to install in."""

    def __init__(self, tmp_path, monkeypatch, version="2026.10.19 linux-x64"):
        self.root = tmp_path.resolve()
        self.mise_log = self.root / "mise.log"
        self.tool_log = self.root / "betterleaks.log"
        self.archive = self.root / "betterleaks.tar.gz"
        self.attempt = self.root / "attempt"
        self.directory = self.attempt / "mise"
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        (self.attempt / "tools").mkdir(parents=True)
        self.contents({"betterleaks": (0o755, binary(self.tool_log))})
        self.mise(version)
        monkeypatch.setenv(
            "PATH", f"{self.root / 'bin'}{os.pathsep}{os.environ['PATH']}"
        )
        self.env = {"PATH": os.environ["PATH"]}

    def contents(self, members):
        archive(self.archive, members)

    def mise(self, version, where=None, extra=""):
        """Write the fake mise.

        where, if given, is what mise where prints in place of the install
        directory, and extra runs after mise install.
        """
        installs = f'"$MISE_DATA_DIR/{INSTALLED}"'
        log = shlex.quote(str(self.mise_log))
        self.executable = script(
            self.root / "bin" / "mise",
            "#!/bin/sh\n"
            f'printf "%s|%s|%s|%s\\n" "$(pwd)" "$MISE_OFFLINE" "$MISE_DATA_DIR" "$*"'
            f" >> {log}\n"
            'case "$2" in\n'
            f"version) env > {shlex.quote(str(self.root / 'version.env'))}\n"
            f"  echo {shlex.quote(version)} ;;\n"
            f"install) mkdir -p {installs} && tar -xzf "
            f"{shlex.quote(str(self.archive))} -C {installs}\n  {extra} ;;\n"
            f"where) echo {shlex.quote(where) if where else installs} ;;\n"
            "esac\n",
        )

    def install(self, tool):
        prepared = release_binary.plan([tool], LINUX, self.env)
        return release_binary.install(
            prepared,
            self.attempt,
            prepared_paths(self.attempt),
            self.worktree,
            self.env,
        )


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


def test_without_a_release_binary_mise_is_never_looked_for(
    tools, tmp_path, monkeypatch
):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert release_binary.plan([tools["pre-commit-hooks"]], LINUX, {}) is None


def test_plan_checks_mise_in_a_scratch_directory_it_removes(
    tools, betterleaks, tmp_path, monkeypatch
):
    machine = Machine(tmp_path, monkeypatch)
    monkeypatch.setenv("MISE_CACHE_DIR", str(machine.root / "cache"))
    env = dict(machine.env, CALLER="kept")
    planned = release_binary.plan([tools["ruff"], betterleaks], LINUX, env)
    archive = betterleaks.release.platforms["linux-x64"]
    assert planned == Mise(
        str(machine.executable),
        env,
        (
            (
                betterleaks,
                NativeTool(
                    "betterleaks", "1.9.0", archive.url, archive.sha256, "betterleaks"
                ),
            ),
        ),
    )
    [line] = logged(machine.mise_log)
    scratch, offline, data, arguments = line.split("|")
    recorded = dict(
        entry.split("=", 1) for entry in logged(machine.root / "version.env")
    )
    assert (offline, arguments) == ("1", "--no-config version")
    assert {
        key: Path(recorded[key]).resolve()
        for key in ("MISE_DATA_DIR", "MISE_STATE_DIR", "MISE_SYSTEM_DATA_DIR")
    } == {
        "MISE_DATA_DIR": Path(scratch, "data").resolve(),
        "MISE_STATE_DIR": Path(scratch, "state").resolve(),
        "MISE_SYSTEM_DATA_DIR": Path(scratch, "system").resolve(),
    }
    assert (recorded["MISE_CACHE_DIR"], recorded["CALLER"]) == (
        str(machine.root / "cache"),
        "kept",
    )
    assert not Path(scratch).exists()


@pytest.mark.parametrize(
    "version", ["2026.10.19 linux-x64 (2026-10-19)", "2027.1.0 macos-arm64"]
)
def test_mise_at_or_after_the_minimum_is_accepted(
    betterleaks, tmp_path, monkeypatch, version
):
    machine = Machine(tmp_path, monkeypatch, version)
    assert release_binary.plan([betterleaks], LINUX, machine.env).tools[0][0] == (
        betterleaks
    )


@pytest.mark.parametrize("version", ["2026.10.18 linux-x64", "", "mise", "2026.10"])
def test_mise_before_the_minimum_or_without_a_version_is_refused(
    betterleaks, tmp_path, monkeypatch, version
):
    machine = Machine(tmp_path, monkeypatch, version)
    failure = refused(release_binary.plan, [betterleaks], LINUX, machine.env)
    assert (failure.code, failure.exit_code) == ("mise_version_mismatch", 4)
    assert release_binary.MINIMUM_MISE in str(failure)


def test_a_machine_without_a_locked_archive_is_unsupported(betterleaks):
    windows = Target("windows-x86_64-none", "windows-x64", "x86_64-pc-windows-msvc")
    failure = refused(release_binary.plan, [betterleaks], windows, {})
    assert failure.code == "unsupported_platform"
    assert all(value in str(failure) for value in ("betterleaks", "windows-x64"))


def test_install_links_the_installed_executable_and_probes_it(
    betterleaks, tmp_path, monkeypatch
):
    monkeypatch.delenv("MISE_CACHE_DIR", raising=False)
    machine = Machine(tmp_path, monkeypatch)
    location = machine.directory / "data" / INSTALLED
    assert machine.install(betterleaks) == {
        "betterleaks": (location, PreparedTool("1.9.0", location / "betterleaks"))
    }
    link = machine.attempt / "tools" / "gateseal-betterleaks"
    assert os.readlink(link) == f"../mise/data/{INSTALLED}/betterleaks"
    data = machine.directory / "data"
    assert logged(machine.mise_log)[1:] == [
        f"{machine.directory}||{data}|--no-config install {spec()}",
        f"{machine.directory}|1|{data}|--no-config where {spec()}",
    ]
    assert logged(machine.tool_log) == [
        f"{machine.worktree}|version",
        f"{machine.worktree}|{PROBE}",
    ]


@pytest.mark.parametrize(
    "where,extra",
    [
        ("mise/data/" + INSTALLED, ""),
        ("{directory}/linked", f'ln -s "$MISE_DATA_DIR/{INSTALLED}" linked'),
        ("{directory}/data/" + INSTALLED + "/betterleaks", ""),
        ("{root}/elsewhere", ""),
    ],
    ids=["relative", "symlink", "file", "outside the attempt"],
)
def test_mise_must_install_into_a_directory_of_the_attempt(
    betterleaks, tmp_path, monkeypatch, where, extra
):
    machine = Machine(tmp_path, monkeypatch)
    (machine.root / "elsewhere").mkdir()
    where = where.format(directory=machine.directory, root=machine.root)
    machine.mise("2026.10.19", where, extra)
    monkeypatch.chdir(machine.attempt)
    failure = refused(machine.install, betterleaks)
    assert (failure.code, failure.exit_code) == ("invalid_preparation", 4)
    assert "outside" in str(failure)
    assert where in str(failure)
    assert not os.path.lexists(machine.attempt / "tools" / "gateseal-betterleaks")


@pytest.mark.parametrize(
    "members",
    [
        {"LICENSE": (0o644, "MIT\n")},
        {"bin/betterleaks": (0o755, "exit 0\n"), "betterleaks": "bin/betterleaks"},
        {"betterleaks": (0o644, "exit 0\n")},
    ],
    ids=["missing", "symlink", "not executable"],
)
def test_mise_must_install_the_executable_as_a_regular_file(
    betterleaks, tmp_path, monkeypatch, members
):
    machine = Machine(tmp_path, monkeypatch)
    machine.contents(members)
    failure = refused(machine.install, betterleaks)
    assert (failure.code, failure.exit_code) == ("invalid_preparation", 4)
    executable = machine.directory / "data" / INSTALLED / "betterleaks"
    assert str(executable) in str(failure)


def test_install_refuses_an_executable_of_another_version(
    betterleaks, tmp_path, monkeypatch
):
    machine = Machine(tmp_path, monkeypatch)
    machine.contents({"betterleaks": (0o755, binary(machine.tool_log, "1.8.0"))})
    failure = refused(machine.install, betterleaks)
    assert (failure.code, failure.exit_code) == ("tool_failed", 4)
    assert failure.next_step == "gateseal activate"
    assert all(value in str(failure) for value in ("betterleaks", "1.8.0", "1.9.0"))


def recorded(root, native_tools):
    """Return a record of an attempt in root whose tools hold a linked fake.

    The record names another fake as each tool's executable, which logs to
    root/unlinked, so a probe that skips the link leaves root/log empty.
    """
    attempt = root / "attempt"
    script(attempt / "betterleaks", binary(root / "log"))
    executable = script(attempt / "unlinked", binary(root / "unlinked"))
    tools = attempt / "tools"
    tools.mkdir()
    (tools / "gateseal-betterleaks").symlink_to("../betterleaks")
    prepared = {
        name: PreparedTool(version, executable) for name, version in native_tools
    }
    return PreparedRecord(
        attempt,
        root,
        "0" * 64,
        "linux-x64",
        prepared_paths(attempt),
        (),
        prepared,
        None,
        {},
        {},
        None,
    )


def probed(tools, root, *native_tools):
    record = recorded(root, native_tools)
    return release_binary.probe(tools, record, root, dict(os.environ))


def test_probe_reproves_each_recorded_tool_through_its_link(betterleaks, tmp_path):
    root = tmp_path.resolve()
    probed([betterleaks], root, ("betterleaks", "1.9.0"))
    assert logged(root / "log") == [f"{root}|version", f"{root}|{PROBE}"]
    assert logged(root / "unlinked") == []


def test_probe_proves_the_recorded_version(betterleaks, tmp_path):
    failure = refused(
        probed, [betterleaks], tmp_path.resolve(), ("betterleaks", "1.8.0")
    )
    assert (failure.code, failure.exit_code) == ("tool_failed", 4)
    assert all(value in str(failure) for value in ("betterleaks", "1.8.0", "1.9.0"))


def test_probe_refuses_a_recorded_tool_the_lock_does_not_select(tools, tmp_path):
    failure = refused(
        probed, [tools["ruff"]], tmp_path.resolve(), ("betterleaks", "1.9.0")
    )
    assert (failure.code, failure.exit_code) == ("needs_activation", 3)
    assert failure.next_step == "gateseal activate"
    assert "betterleaks" in str(failure)
