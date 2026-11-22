"""Each provider's job, run twice on one agent that keeps its home folder.

The bash steps of the generated file run as the provider's agent runs them,
with its variables, its expansions and its way of extending PATH, for a manual
run, which checks every file. Each run gets a fresh temporary folder, as a
self-hosted agent gives each job. The agent's
global Git configuration rewrites gateseal's repository to the test's stand-in,
so the job reaches the gateseal that the Lock pins only through that file.
"""

import hashlib
import os
import platform
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml
from conftest import TRACKED
from gateseal.ci_providers import PROVIDERS
from gateseal.compiler_release import REPOSITORY
from installation import process, success

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        (platform.system(), platform.machine()) != ("Linux", "x86_64"),
        reason="the gate job runs only on Linux on x86-64",
    ),
]

NETWORK = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "SSL_CERT_FILE",
    "CURL_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "PIP_CERT",
    "GIT_SSL_CAINFO",
    "UV_NATIVE_TLS",
    "UV_SYSTEM_CERTS",
)
"""The proxy and certificate variables that an agent's administrator sets."""

AZURE_MACRO = re.compile(r"\$\(([^)]+)\)")
GITHUB_EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
PREPEND = "##vso[task.prependpath]"
GLOBAL = "git config --global --list"


def bash_steps(steps, key, expand):
    """Return each bash step's script and its environment, expanded."""
    return [
        (
            step[key],
            {name: expand(str(value)) for name, value in step.get("env", {}).items()},
        )
        for step in steps
        if key in step
    ]


def azure_steps(document, head):
    variables = {"Build.Reason": "Manual", "Build.SourceVersion": head}
    [job] = document["jobs"]
    return bash_steps(
        job["steps"],
        "bash",
        lambda value: AZURE_MACRO.sub(lambda m: variables.get(m[1], m[0]), value),
    )


def github_steps(document, head):
    context = {"github.event_name": "workflow_dispatch", "github.sha": head}
    return bash_steps(
        document["jobs"]["gateseal"]["steps"],
        "run",
        lambda value: GITHUB_EXPRESSION.sub(lambda m: context.get(m[1], ""), value),
    )


def azure_path(output, path_file):
    """Return the folders that a step's logging commands put on PATH."""
    return [
        line.removeprefix(PREPEND)
        for line in output.splitlines()
        if line.startswith(PREPEND)
    ]


def github_path(output, path_file):
    """Return the folders that a step wrote to its GITHUB_PATH file."""
    return path_file.read_text().splitlines()


@dataclass(frozen=True)
class Agent:
    """How one provider's agent runs the bash steps of a job."""

    temporary: str
    running: tuple[str, str]
    steps: Callable[[dict, str], list[tuple[str, dict[str, str]]]]
    added: Callable[[str, Path], list[str]]


AGENTS = {
    "azure": Agent(
        "AGENT_TEMPDIRECTORY", ("TF_BUILD", "True"), azure_steps, azure_path
    ),
    "github": Agent(
        "RUNNER_TEMP", ("GITHUB_ACTIONS", "true"), github_steps, github_path
    ),
}


def commit_ci_files(installed, consumer, provider):
    """Configure consumer for provider, commit its files, and return its commit."""
    installed.cli(consumer, "configure", "--capability", "hygiene", "--ci", provider)
    success(["git", "add", *TRACKED, PROVIDERS[provider].path], consumer)
    success(["git", "commit", "--no-verify", "-q", "-m", "Adopt gateseal"], consumer)
    return success(["git", "rev-parse", "HEAD"], consumer).stdout.strip()


def agent_home(directory, origin):
    """Return an agent home with real settings in both global Git files."""
    home = directory / "agent home"
    (home / ".config" / "git").mkdir(parents=True)
    (home / ".gitconfig").write_text(
        f'[url "{origin.as_uri()}"]\n\tinsteadOf = {REPOSITORY}\n'
        "[maintenance]\n\tauto = false\n"
        f"[init]\n\ttemplateDir = {home / 'own template'}\n"
        f"[core]\n\thooksPath = {home / 'own hooks'}\n"
    )
    (home / ".config" / "git" / "config").write_text(
        "[init]\n\tdefaultBranch = main\n[advice]\n\tdetachedHead = false\n"
    )
    return home


def state(home):
    """Return every entry under home with its mode, size, content hash and mtime,
    and the global Git configuration that Git lists with home as HOME."""
    entries = {}
    for path in [home, *home.rglob("*")]:
        info = path.lstat()
        regular = path.is_file() and not path.is_symlink()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if regular else None
        entries[str(path.relative_to(home))] = (
            info.st_mode,
            info.st_size if regular else None,
            digest,
            info.st_mtime_ns,
        )
    entries[GLOBAL] = success(
        ["git", "config", "--global", "--list"],
        home,
        {"HOME": str(home), "PATH": os.environ["PATH"]},
    ).stdout
    return entries


def changed(before, after):
    """Return the entries that differ between two states, sorted."""
    return sorted(
        name
        for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    )


def run_job(agent, steps, root, home, temporary):
    """Run steps in root on an agent with home, and fail on the first failed step."""
    path = os.environ["PATH"]
    for script, env in steps:
        path_file = temporary.parent / f"{temporary.name} GITHUB_PATH"
        path_file.write_text("")
        environment = {
            **{name: os.environ[name] for name in NETWORK if name in os.environ},
            "HOME": str(home),
            "PATH": path,
            "LANG": "C.UTF-8",
            agent.temporary: str(temporary),
            "GITHUB_PATH": str(path_file),
            agent.running[0]: agent.running[1],
            **env,
        }
        ran = process(
            [shutil.which("bash"), "--noprofile", "--norc", "-c", script],
            root,
            environment,
        )
        assert ran.returncode == 0, ran.stdout + ran.stderr
        added = agent.added(ran.stdout, path_file)
        path = os.pathsep.join([*reversed(added), path])


@pytest.mark.parametrize("provider", sorted(AGENTS))
def test_two_runs_leave_the_agents_home_and_global_git_configuration(
    provider, installed, consumer, gateseal_origin, tmp_path
):
    head = commit_ci_files(installed, consumer, provider)
    document = yaml.safe_load((consumer / PROVIDERS[provider].path).read_bytes())
    agent = AGENTS[provider]
    steps = agent.steps(document, head)
    assert len(steps) == 3
    home = agent_home(tmp_path, gateseal_origin)
    before = state(home)
    assert f"insteadof={REPOSITORY}" in before[GLOBAL]
    for run in ("first", "second"):
        temporary = tmp_path / f"{run} run"
        temporary.mkdir()
        run_job(agent, steps, consumer, home, temporary)
        assert changed(before, state(home)) == [], f"after the {run} run"
