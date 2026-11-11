"""The cleaned process environments that activation and hooks run in.

Every process gateseal starts for a tool gets one of them, so a variable of
the user's shell, such as a RUFF_ or BETTERLEAKS_ setting or a uv override,
cannot change what activation installs or what a hook checks.
"""

import os
from collections.abc import Iterable
from pathlib import Path

from gateseal import bootstrap
from gateseal.processes import execute, find_python
from gateseal.tools import removed_prefixes
from gateseal.tools.model import Tool


def tool_environment(tools: Iterable[Tool] = ()) -> dict[str, str]:
    """Return the cleaned environment without the settings of built-ins or tools.

    Every process that activation and the hooks start gets it, so a RUFF_ or
    BETTERLEAKS_ variable cannot change what activation probes or what a hook
    checks. GIT_CONFIG makes git config, and no other command, read only the
    file it names, so with it gateseal would miss settings that a commit obeys.
    """
    prefixes = (
        *removed_prefixes(),
        *(p for t in tools for p in t.definition.removed_prefixes),
    )
    return {
        key: value
        for key, value in bootstrap.clean_environment().items()
        if key != "GIT_CONFIG" and not key.startswith(prefixes)
    }


def uv_environment(tools: Iterable[Tool] = ()) -> dict[str, str]:
    env = tool_environment(tools)
    env.pop("UV_OFFLINE", None)
    env.pop("UV_PYTHON_DOWNLOADS", None)
    for name in ("UV_CACHE_DIR", "UV_PYTHON_CACHE_DIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    env.update(
        {
            "UV_PYTHON_PREFERENCE": "only-managed",
            "UV_HTTP_CONNECT_TIMEOUT": "10",
            "UV_HTTP_TIMEOUT": "30",
            "UV_HTTP_RETRIES": "1",
            "UV_NO_PROGRESS": "1",
        }
    )
    return env


def package_environment(
    uv: str,
    root: Path,
    env: dict[str, str],
    interpreter: str,
    destination: Path,
    requirements: Path,
) -> str:
    execute(
        [
            uv,
            "venv",
            "--no-config",
            "--no-python-downloads",
            "--python",
            interpreter,
            str(destination),
        ],
        root,
        env,
    )
    python = find_python(uv, root, env, str(destination))
    execute(
        [
            uv,
            "pip",
            "install",
            "--no-config",
            "--no-python-downloads",
            "--python",
            python,
            "--require-hashes",
            "--only-binary",
            ":all:",
            "--index-url",
            "https://pypi.org/simple",
            "-r",
            str(requirements),
        ],
        root,
        env,
    )
    return python


def pre_commit_environment(
    paths: bootstrap.PreparedPaths, tools: Iterable[Tool]
) -> dict[str, str]:
    env = tool_environment(tools)
    env["PRE_COMMIT_HOME"] = str(paths.pre_commit_home)
    env["PATH"] = os.pathsep.join(
        [
            str(paths.tools),
            str(paths.pre_commit_python.parent),
            str(paths.git.parent),
            "/usr/bin",
            "/bin",
        ]
    )
    return env
