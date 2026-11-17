"""The cleaned process environments that gateseal starts Git, uv and mise in.

A variable of the user's shell, such as a uv override, then cannot change
what gateseal reads or installs. pre-commit and its hooks do not get one:
they run in the caller's environment, as they do at git commit.
"""

import os


def clean_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            (
                "PYTHON",
                "UV_",
                "PIP_",
                "VIRTUALENV_",
                "CONDA",
                "DYLD_",
                "MISE_",
            )
        )
        and key
        not in ("VIRTUAL_ENV", "__PYVENV_LAUNCHER__", "LD_PRELOAD", "LD_LIBRARY_PATH")
    }


def tool_environment() -> dict[str, str]:
    """Return the cleaned environment.

    GIT_CONFIG makes git config, and no other command, read only the file it
    names, so with it gateseal would miss settings that a commit obeys.
    """
    return {
        key: value for key, value in clean_environment().items() if key != "GIT_CONFIG"
    }


def uv_environment() -> dict[str, str]:
    env = tool_environment()
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
