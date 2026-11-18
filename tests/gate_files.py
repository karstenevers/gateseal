"""Render each CI provider's gate file and read it through its own syntax."""

import os
from dataclasses import dataclass

import yaml
from conftest import PIN
from gateseal.ci_providers import PROVIDERS
from gateseal.lock import compile_outputs
from gateseal.manifest import BundledSource, git_source, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from installation import process

POLICY = load_bundled_policy()
BRANCH = "release/2.x"

SERVED = {
    "azure": "https://dev.azure.com/team/platform/_git/gateseal-policy",
    "github": "https://github.com/team/gateseal-policy.git",
}
"""A Git policy URL that each provider's file serves a token to."""


@dataclass(frozen=True)
class Step:
    script: str
    env: dict[str, str]


@dataclass(frozen=True)
class GateFile:
    """One provider's file: its bytes, its document and its three steps."""

    data: bytes
    document: dict
    checkout: dict
    prepare: Step
    gate: Step


def manifest(provider, branch=BRANCH):
    table = f'\n[ci]\nprovider = "{provider}"\ndefault_branch = "{branch}"\n'
    return render(("hygiene", "secrets"), BundledSource(POLICY.release)) + (
        table.encode()
    )


def policy(repository):
    return git_source(repository, ".", "c" * 40, "v1.0.0")


def gate_file(provider, repository=None, branch=BRANCH, pin=PIN):
    """Return provider's file for a bundled policy, or a Git policy at repository."""
    source = policy(repository) if repository else BundledSource(POLICY.release)
    parsed = parse_manifest(manifest(provider, branch))
    data = compile_outputs(resolve(parsed, POLICY), pin, source)[
        PROVIDERS[provider].path
    ]
    document = yaml.safe_load(data)
    if provider == "github":
        checkout, prepare, gate = document["jobs"]["gateseal"]["steps"]
        script = "run"
    else:
        [job] = document["jobs"]
        checkout, prepare, gate = job["steps"]
        script = "bash"
    return GateFile(
        data,
        document,
        checkout,
        Step(prepare[script], prepare["env"]),
        Step(gate[script], gate["env"]),
    )


def credential(step, url, tokens, cwd):
    """Ask Git for url's credential with step's configuration and tokens.

    Returns the password that the step's helper gives, or None when no helper
    serves url. Git never prompts. The agent's own global configuration holds a
    helper that answers every URL with the password "agent", as a CI agent's
    can.
    """
    scheme, _, rest = url.partition("://")
    host, _, path = rest.partition("/")
    asked = f"protocol={scheme}\nhost={host}\npath={path}\n\n"
    agent = cwd / "agent.gitconfig"
    answer = "!f() { echo username=agent; echo password=agent; }; f"
    agent.write_text(f'[credential]\n\thelper = "{answer}"\n')
    configuration = {k: v for k, v in step.env.items() if k.startswith("GIT_CONFIG")}
    environment = (
        os.environ
        | {"GIT_CONFIG_GLOBAL": str(agent), "GIT_TERMINAL_PROMPT": "0"}
        | configuration
        | tokens
    )
    filled = process(
        ["sh", "-c", 'printf "%s" "$1" | git credential fill', "sh", asked],
        cwd,
        environment,
    )
    lines = dict(
        line.split("=", 1) for line in filled.stdout.splitlines() if "=" in line
    )
    return lines.get("password")
