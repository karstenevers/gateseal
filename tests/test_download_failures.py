"""Name the step that fixes a failed download of the compiler or its Python.

A proxy at a closed port refuses every connection, as a machine without network
does, and the private fixture repository answers a request without credentials
with HTTP 404.
"""

import json
import platform
import shutil

import pytest
from conftest import unreachable
from gateseal.compiler_release import TARGETS
from installation import PACKAGE

pytestmark = pytest.mark.integration

PRIVATE = json.loads((PACKAGE / "tests/artifact-source.json").read_bytes())["url"]


def private_compiler(installed, root):
    installed.cli(
        root,
        "configure",
        "--capability",
        "hygiene",
        "--compiler-source",
        PRIVATE,
        "--compiler-sha256",
        installed.checksum,
    )
    return root


def saved_copy(installed, directory):
    """Save a copy of installed's wheel under its own name, as a download does."""
    directory.mkdir()
    return shutil.copyfile(installed.wheel, directory / installed.wheel.name)


def test_unreachable_python_names_network_access(installed, configured, tmp_path):
    archives = tmp_path / "empty archives"
    env = unreachable(
        dict(
            installed.environment,
            UV_PYTHON_CACHE_DIR=str(archives),
            GATESEAL_INTERPRETER_STORE=str(tmp_path / "empty store"),
        )
    )
    failed = installed.cli(configured, "activate", code=4, env=env)
    lock = json.loads((configured / "gateseal.lock").read_bytes())
    target = TARGETS[(platform.system(), platform.machine())]
    [url] = [
        record["url"]
        for key, record in lock["runtime"]["python"].items()
        if key.endswith(target.python)
    ]
    assert url in failed["diagnostics"][0]["message"]
    assert failed["next_steps"] == [
        f"Give this machine network access to {url}, then run gateseal activate."
    ]


def test_unreachable_compiler_names_a_local_wheel(
    installed, prepared, consumer, tmp_path
):
    """The prepared fixture stores the Python, so only the compiler download fails."""
    root = private_compiler(installed, consumer)
    lock = (root / "gateseal.lock").read_bytes()
    failed = installed.cli(
        root, "activate", code=4, env=unreachable(installed.environment)
    )
    assert failed["next_steps"] == [
        f"Give this machine network access to {PRIVATE}, then run gateseal "
        f"activate. Or save {PRIVATE} as {installed.wheel.name} and run gateseal "
        "activate --compiler-wheel with its absolute path."
    ]
    copy = saved_copy(installed, tmp_path / "downloads")
    activated = installed.cli(root, "activate", "--compiler-wheel", copy)
    assert activated["code"] == "prepared"
    assert (root / "gateseal.lock").read_bytes() == lock


@pytest.mark.network
def test_private_compiler_without_credentials_names_netrc(
    installed, consumer, tmp_path
):
    root = private_compiler(installed, consumer)
    anonymous = dict(installed.environment, NETRC=str(tmp_path / "no credentials"))
    failed = installed.cli(root, "activate", code=4, env=anonymous)
    assert "404 Not Found" in failed["diagnostics"][0]["message"]
    assert failed["next_steps"] == [
        "Set NETRC to a mode-0600 file that holds machine raw.githubusercontent.com "
        f"login __token__ password TOKEN, with a TOKEN that can read {PRIVATE}, "
        f"then run gateseal activate. Or save {PRIVATE} as {installed.wheel.name} "
        "and run gateseal activate --compiler-wheel with its absolute path."
    ]
    copy = saved_copy(installed, tmp_path / "downloads")
    activated = installed.cli(root, "activate", "--compiler-wheel", copy, env=anonymous)
    assert activated["code"] == "prepared"
