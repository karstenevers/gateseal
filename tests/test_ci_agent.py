"""The preparation step on an agent: the machine it needs and the agent's Git.

The step runs here as the agent runs it. Stand-ins for uname, getconf, tar and
sha256sum describe a machine that meets the requirement, whatever runs the
tests, and a stand-in curl records what the step's Git reads, then fails the
download. So no test here downloads anything.
"""

import os
import shutil

import pytest
from gate_files import gate_file
from installation import process

EACH = pytest.mark.parametrize("provider", ["azure", "github"])
TEMPORARY = {"azure": "AGENT_TEMPDIRECTORY", "github": "RUNNER_TEMP"}
BASH = shutil.which("bash")
REQUIREMENT = "gateseal: the gate job needs Linux on x86-64 with glibc,"

CURL = """#!/bin/sh
{
  echo "$HOME"
  git config --get-all gateseal.source
  git config --global gateseal.written yes
  git config --get gateseal.written
} >"$RECORD"
exit 22
"""
"""A curl that records the step's home, the gateseal.source values its Git
reads, and a global setting it writes, then fails."""

MACHINE = {
    "curl": CURL,
    "uname": '#!/bin/sh\necho "Linux x86_64"\n',
    "getconf": "#!/bin/sh\necho glibc 2.39\n",
    "tar": "#!/bin/sh\n",
    "sha256sum": "#!/bin/sh\n",
}
"""The stand-ins of a machine that meets the requirement, by name."""


def stand_ins(directory, **scripts):
    """Return directory holding MACHINE's stand-ins, each of scripts replacing one."""
    directory.mkdir(exist_ok=True)
    for name, text in (MACHINE | scripts).items():
        (directory / name).write_text(text)
        (directory / name).chmod(0o755)
    return directory


def prepare(provider, tmp_path, path=None, **agent):
    """Run provider's preparation on path, with agent's further variables.

    path defaults to MACHINE's stand-ins before the agent's own folders.
    Returns the step's exit code, its stderr, and the lines that the stand-in
    curl recorded, or None when the step started no download.
    """
    if path is None:
        path = f"{stand_ins(tmp_path / 'bin')}{os.pathsep}{os.environ['PATH']}"
    temporary = tmp_path / "agent temp"
    temporary.mkdir(exist_ok=True)
    record = tmp_path / "curl record"
    step = gate_file(provider).prepare
    environment = {
        "PATH": path,
        TEMPORARY[provider]: str(temporary),
        "RECORD": str(record),
        **step.env,
        **agent,
    }
    ran = process([BASH, "-c", step.script], tmp_path, environment)
    lines = record.read_text().splitlines() if record.exists() else None
    return ran.returncode, ran.stderr, lines


def agent_home(tmp_path):
    """Return an agent home whose name Git must quote, with both global files."""
    home = tmp_path / 'agent "home" \\ ü'
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".gitconfig").write_text("[gateseal]\n\tsource = home\n")
    (home / ".config" / "git" / "config").write_text("[gateseal]\n\tsource = xdg\n")
    return home


def contents(home):
    return {
        path.relative_to(home): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }


@EACH
@pytest.mark.parametrize(
    "uname,machine",
    [
        ("Darwin arm64", "Darwin on arm64"),
        ("Linux aarch64", "Linux on aarch64"),
        ("FreeBSD amd64", "FreeBSD on amd64"),
    ],
)
def test_another_system_or_architecture_stops_before_any_download(
    provider, uname, machine, tmp_path
):
    getconf = "#!/bin/sh\nexit 1\n"
    uname = f'#!/bin/sh\necho "{uname}"\n'
    bin = stand_ins(tmp_path / "bin", uname=uname, getconf=getconf)
    code, error, record = prepare(provider, tmp_path, str(bin))
    assert (code, record) == (1, None)
    assert error == f"{REQUIREMENT} and this machine is {machine}.\n"
    assert not (tmp_path / "agent temp" / "gateseal-tools").exists()


@EACH
def test_linux_on_x86_64_without_glibc_stops_before_any_download(provider, tmp_path):
    getconf = "#!/bin/sh\necho unsupported >&2\nexit 1\n"
    bin = stand_ins(tmp_path / "bin", getconf=getconf)
    code, error, record = prepare(provider, tmp_path, str(bin))
    assert (code, record) == (1, None)
    assert error == (
        f"{REQUIREMENT} and this machine is Linux on x86_64 without glibc.\n"
    )


@EACH
def test_the_jobs_git_reads_the_agents_files_and_writes_only_its_own(
    provider, tmp_path
):
    home = agent_home(tmp_path)
    before = contents(home)
    code, error, record = prepare(provider, tmp_path, HOME=str(home))
    assert code == 22, error
    job = tmp_path / "agent temp" / "gateseal-home"
    assert record == [str(job), "xdg", "home", "yes"]
    assert contents(home) == before


@EACH
def test_the_jobs_git_reads_the_agents_xdg_folder(provider, tmp_path):
    home = agent_home(tmp_path)
    xdg = tmp_path / "agent config"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "config").write_text("[gateseal]\n\tsource = elsewhere\n")
    _, error, record = prepare(
        provider, tmp_path, HOME=str(home), XDG_CONFIG_HOME=str(xdg)
    )
    assert record is not None, error
    assert record[1:] == ["elsewhere", "home", "yes"]


@EACH
def test_the_jobs_git_reads_the_file_that_git_config_global_names(provider, tmp_path):
    home = agent_home(tmp_path)
    named = tmp_path / 'named "global" \\ config'
    named.write_text("[gateseal]\n\tsource = named\n")
    before = named.read_bytes()
    _, error, record = prepare(
        provider, tmp_path, HOME=str(home), GIT_CONFIG_GLOBAL=str(named)
    )
    assert record is not None, error
    assert record[1:] == ["named", "yes"]
    assert named.read_bytes() == before


AGENT_STORAGE = {
    "PRE_COMMIT_HOME": "pre-commit",
    "UV_CACHE_DIR": "uv cache",
    "UV_PYTHON_CACHE_DIR": "uv python cache",
    "UV_PYTHON_INSTALL_DIR": "uv python",
    "UV_CONFIG_FILE": "uv.toml",
    "UV_PROJECT_ENVIRONMENT": "uv project",
    "PIP_CACHE_DIR": "pip cache",
    "VIRTUALENV_OVERRIDE_APP_DATA": "virtualenv data",
    "MISE_CACHE_DIR": "mise cache",
    "MISE_CONFIG_DIR": "mise config",
    "MISE_DATA_DIR": "mise data",
    "MISE_STATE_DIR": "mise state",
    "MISE_INSTALLS_DIR": "mise installs",
    "MISE_SYSTEM_DATA_DIR": "mise system",
    "GIT_TEMPLATE_DIR": "git template",
}
"""Variables an agent may set that would move a tool's storage or Git's template."""

STORAGE_CURL = """#!/bin/sh
{
  env | grep -E '^(PRE_COMMIT_HOME|(UV|PIP|VIRTUALENV|MISE)_[A-Z_]*)=' | sort
  env | grep -E '^(GIT_TEMPLATE_DIR|SKIP)='
  env | grep -E '^XDG_'
} >"$RECORD"
exit 22
"""


@EACH
def test_the_job_drops_the_agents_storage_template_mise_settings_and_skips(
    provider, tmp_path
):
    home = agent_home(tmp_path)
    agent = {name: str(tmp_path / value) for name, value in AGENT_STORAGE.items()}
    agent |= {"MISE_EXEC_AUTO_INSTALL": "false", "MISE_AUTO_INSTALL": "false"}
    agent["SKIP"] = "betterleaks"
    agent["XDG_DATA_HOME"] = str(tmp_path / "agent data")
    bin = stand_ins(tmp_path / "bin", curl=STORAGE_CURL)
    path = f"{bin}{os.pathsep}{os.environ['PATH']}"
    code, error, record = prepare(provider, tmp_path, path, HOME=str(home), **agent)
    assert code == 22, error
    assert record is not None
    temporary = tmp_path / "agent temp"
    assert record == [f"MISE_SYSTEM_DATA_DIR={temporary}/gateseal-home/mise-system"]
