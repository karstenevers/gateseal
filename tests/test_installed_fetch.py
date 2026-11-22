import shutil

import pytest
from conftest import BUNDLED_TOOLS, on_machine, with_git_config
from gateseal import fetch
from gateseal.platforms import machine
from installation import clone, success

pytestmark = pytest.mark.integration

BETTERLEAKS = BUNDLED_TOOLS["betterleaks"]
NAME = f"betterleaks {BETTERLEAKS.pin}"
URL = BETTERLEAKS.release.platforms[machine()].url
HOOKS = "https://github.com/pre-commit/pre-commit-hooks"


@pytest.fixture(scope="module")
def secured(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("fetch consumer ü").resolve()
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer space ü ")
        capabilities = ("--capability", "hygiene", "--capability", "secrets")
        installed.cli(consumer, "configure", *capabilities)
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-m", "Adopt"], consumer)
    return consumer


def without_installs(installed, tmp_path, **changes):
    return dict(
        installed.environment,
        MISE_DATA_DIR=str(tmp_path / "mise data"),
        MISE_STATE_DIR=str(tmp_path / "mise state"),
        **changes,
    )


def downloads(result):
    return [d for d in result["diagnostics"] if d["category"] == "download"]


def test_fetch_installs_what_the_gate_then_starts(installed, secured, tmp_path):
    env = without_installs(installed, tmp_path)
    binary, hooks = downloads(installed.cli(secured, "fetch", env=env))
    assert binary["message"] == f"mise installed {NAME} from {URL}."
    assert hooks["message"].startswith("pre-commit built the hook environments")
    assert list((tmp_path / "mise data").glob("installs/http-betterleaks-*/*"))
    assert installed.cli(secured, "gate", env=env)["code"] == "checks_passed"
    again = downloads(installed.cli(secured, "fetch", env=env))
    assert [d["attempts"] for d in again] == [1, 1]


@pytest.mark.parametrize("command", ["fetch", "gate"])
def test_a_release_binary_that_cannot_be_downloaded_is_no_failed_check(
    installed, secured, tmp_path, command
):
    env = without_installs(
        installed, tmp_path, MISE_CACHE_DIR=str(tmp_path / "empty cache")
    )
    result = installed.cli(secured, command, code=4, env=env)
    message, output = (d["message"] for d in result["diagnostics"])
    assert result["code"] == "download_failed"
    assert message == (
        f"mise could not install {NAME} from {URL} in 3 attempts, so the code was "
        "not checked."
    )
    assert "offline mode is enabled" in output
    assert result["next_steps"] == [fetch.RERUN]
    assert not list((tmp_path / "mise data").glob("installs/*"))


@pytest.mark.parametrize("command", ["fetch", "gate"])
def test_a_hook_environment_that_cannot_be_built_is_no_failed_check(
    installed, secured, tmp_path, command
):
    env = with_git_config(
        dict(installed.environment, PRE_COMMIT_HOME=str(tmp_path / "pre-commit home")),
        {"url.file:///missing/.insteadOf": HOOKS},
    )
    result = installed.cli(secured, command, code=4, env=env)
    message, output = (d["message"] for d in result["diagnostics"])
    assert result["code"] == "download_failed"
    assert message == (
        f"pre-commit could not build the hook environment of {HOOKS} in 3 "
        "attempts, so the code was not checked."
    )
    assert "/missing/" in output
    assert result["next_steps"] == [fetch.RERUN]


def test_a_download_that_fails_once_succeeds_at_the_second_attempt(
    installed, secured, tmp_path
):
    failed = tmp_path / "failed"
    mise = tmp_path / "bin" / "mise"
    mise.parent.mkdir()
    mise.write_text(
        "#!/bin/sh\n"
        f'case " $* " in *" install "*) if [ ! -e "{failed}" ]; then\n'
        f'  : > "{failed}"; echo "connection reset" >&2; exit 1\n'
        "fi ;; esac\n"
        f'exec "{shutil.which("mise")}" "$@"\n'
    )
    mise.chmod(0o755)
    path = f"{mise.parent}:{installed.environment['PATH']}"
    env = without_installs(installed, tmp_path, PATH=path)
    binary, hooks = downloads(installed.cli(secured, "fetch", env=env))
    assert failed.exists()
    assert (binary["attempts"], hooks["attempts"]) == (2, 1)
    assert binary["message"].endswith(" The download took 2 attempts.")
    assert list((tmp_path / "mise data").glob("installs/http-betterleaks-*/*"))
