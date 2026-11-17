"""The commit check compares each installed tool it checks with the staged Lock.

Betterleaks is checked at commit. In this process the hook asks a stand-in
mise where Betterleaks is installed and which program mise starts, and a test
fills that folder. The installed test commits through pre-commit with the real
mise and Betterleaks, swaps the program that mise starts, and commits again.
"""

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
from pathlib import Path

import pytest
from conftest import stderr_refusal
from gateseal import commit_check
from gateseal.platforms import machine
from gateseal.worktree import locate_worktree
from installation import clone, process, success
from stand_ins import configure, executable
from test_secrets import adopt, assert_blocked, stage_secret, without_mise

LOCK = "gateseal.lock"
SWAPPED = "#!/bin/sh\necho 1.9.0\n"
"""A Betterleaks that reports its version and finds nothing."""


def request(root):
    """Return mise's request for Betterleaks, as its hook's entry names it."""
    config = json.loads((root / ".pre-commit-config.yaml").read_text())
    [entry] = [
        hook["entry"]
        for repository in config["repos"]
        for hook in repository["hooks"]
        if hook["id"] == "betterleaks"
    ]
    words = shlex.split(entry)
    return words[words.index("--") - 1]


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A clone whose index holds what configure wrote for secrets, as the cwd."""
    root = clone(tmp_path / "origin", tmp_path / "consumer ü")
    configure(locate_worktree(root), ["hygiene", "secrets"])
    success(["git", "add", "gateseal.toml", LOCK, ".pre-commit-config.yaml"], root)
    success(["git", "add", ".gateseal"], root)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def folder(tmp_path, monkeypatch):
    """Where a stand-in mise on PATH says Betterleaks is installed, once it is.

    As mise does, it starts a program from the install's bin folder when the
    install has one.
    """
    folder = tmp_path / "mise installs" / "betterleaks 1.9.0"
    mise = executable(
        tmp_path / "stand-in" / "mise",
        f"#!/bin/sh\nif [ ! -d '{folder}' ]; then\n"
        "echo 'mise ERROR betterleaks@1.9.0 not installed' >&2; exit 1; fi\n"
        f"if [ \"$2\" = where ]; then echo '{folder}'; exit; fi\n"
        f'bin=\'{folder}\'; if [ -d "$bin/bin" ]; then bin="$bin/bin"; fi\n'
        'if [ -e "$bin/$3" ]; then echo "$bin/$3"; else\n'
        'echo "mise ERROR $3 is a mise bin however it is not currently active" >&2\n'
        "exit 1; fi\n",
    )
    monkeypatch.setenv("PATH", f"{mise.parent}{os.pathsep}{os.environ['PATH']}")
    return folder


def install(folder, program, at="betterleaks"):
    """Install program as Betterleaks at the path at in folder."""
    executable(folder / at, program)


def edit_record(root, change):
    """Stage a Lock whose record of this machine's Betterleaks change edits."""
    path = root / LOCK
    document = json.loads(path.read_text())
    change(document["native_artifacts"]["betterleaks"]["platforms"][machine().mise])
    path.write_text(json.dumps(document))
    success(["git", "add", LOCK], root)


def record(root, program):
    """Stage a Lock that records program as this machine's Betterleaks."""
    sha256 = hashlib.sha256(program.encode()).hexdigest()
    edit_record(root, lambda archive: archive.update(executable_sha256=sha256))


def hook(capsys):
    code = commit_check.main()
    err = capsys.readouterr().err
    return code, stderr_refusal(err) if code else err


def refusal(folder, message):
    return (
        message,
        f"Find out what changed it. Then delete {folder} and commit again, so that "
        "the betterleaks hook installs it again from its checked archive.",
    )


def changed(folder, at="betterleaks"):
    path = folder / at
    return refusal(
        folder, f"{path} is not the betterleaks 1.9.0 program that {LOCK} records."
    )


def starts_none(folder):
    return refusal(
        folder,
        f"mise starts no betterleaks from {folder}, where it installed betterleaks "
        "1.9.0.",
    )


def test_a_tool_that_mise_has_not_installed_passes(staged, folder, capsys):
    assert hook(capsys) == (0, "")


def test_an_install_of_the_recorded_program_passes(staged, folder, capsys):
    install(folder, SWAPPED)
    record(staged, SWAPPED)
    assert hook(capsys) == (0, "")


def test_a_changed_program_fails_and_names_the_tool(staged, folder, capsys):
    install(folder, SWAPPED)
    assert hook(capsys) == (1, changed(folder))


def test_the_program_that_mise_starts_from_the_bin_folder_is_checked(
    staged, folder, capsys
):
    install(folder, SWAPPED)
    record(staged, SWAPPED)
    install(folder, "#!/bin/sh\n", "bin/betterleaks")
    assert hook(capsys) == (1, changed(folder, "bin/betterleaks"))


def test_an_install_without_its_program_fails(staged, folder, capsys):
    folder.mkdir(parents=True)
    assert hook(capsys) == (1, starts_none(folder))


def test_an_empty_bin_folder_fails(staged, folder, capsys):
    """mise exec would start a betterleaks from the rest of PATH."""
    install(folder, SWAPPED)
    record(staged, SWAPPED)
    (folder / "bin").mkdir()
    assert hook(capsys) == (1, starts_none(folder))


def test_a_folder_in_place_of_the_program_fails(staged, folder, capsys):
    (folder / "betterleaks").mkdir(parents=True)
    assert hook(capsys) == (1, changed(folder))


def test_a_record_without_the_programs_sha256_is_not_checked(staged, folder, capsys):
    install(folder, SWAPPED)
    edit_record(staged, lambda archive: archive.pop("executable_sha256"))
    assert hook(capsys) == (0, "")


def test_an_unreadable_record_names_the_field(staged, capsys):
    edit_record(staged, lambda archive: archive.update(note="edited"))
    message, _ = hook(capsys)[1]
    platform = machine().mise
    assert message == (
        f"Cannot read {LOCK}: native_artifacts.betterleaks.platforms.{platform} "
        "requires exactly executable, executable_sha256, sha256, url."
    )


def test_a_mise_that_fails_otherwise_stops_the_commit(
    staged, capsys, monkeypatch, tmp_path
):
    mise = executable(
        tmp_path / "broken" / "mise", "#!/bin/sh\necho broken >&2\nexit 1\n"
    )
    monkeypatch.setenv("PATH", f"{mise.parent}{os.pathsep}{os.environ['PATH']}")
    assert hook(capsys) == (
        1,
        (
            "mise did not say where it installed betterleaks: broken",
            "Fix what mise reports, then commit again.",
        ),
    )


def test_without_mise_no_tool_is_checked(staged, folder, capsys, monkeypatch, tmp_path):
    install(folder, SWAPPED)
    path = without_mise(dict(os.environ), tmp_path / "mirror")["PATH"]
    monkeypatch.setenv("PATH", path)
    assert hook(capsys) == (0, "")


def test_on_a_machine_gateseal_does_not_support_no_tool_is_checked(
    staged, folder, capsys, monkeypatch
):
    install(folder, SWAPPED)
    monkeypatch.setattr(platform, "machine", lambda: "riscv64")
    assert hook(capsys) == (0, "")


@pytest.mark.parametrize(
    "at", ["betterleaks", "bin/betterleaks"], ids=["installed program", "bin folder"]
)
def test_a_commit_with_a_swapped_betterleaks_fails_and_names_it(
    installed, consumer, tmp_path, at
):
    """Without the check, the swapped program lets a secret through.

    mise starts a program in the install's bin folder over the one it installed.
    """
    env = dict(
        installed.environment,
        MISE_DATA_DIR=str(tmp_path / "mise data"),
        MISE_STATE_DIR=str(tmp_path / "mise state"),
    )
    adopt(installed, consumer, env)
    where = ["mise", "--no-config", "where", request(consumer)]
    folder = Path(success(where, consumer, dict(env, MISE_OFFLINE="1")).stdout.strip())
    swapped = folder / at
    swapped.unlink(missing_ok=True)
    executable(swapped, SWAPPED)
    stage_secret(consumer)
    refused = process(["git", "commit", "-m", "Leak"], consumer, env)
    shown = refused.stdout + refused.stderr
    assert refused.returncode == 1, shown
    assert re.search(r"Check files and tools against gateseal\.lock\.+Failed", shown)
    assert f"gateseal: {swapped} is not the betterleaks 1.9.0 program" in shown, shown
    skipped = dict(env, SKIP="gateseal-check")
    success(["git", "commit", "-m", "Leak"], consumer, skipped)
    success(["git", "reset", "--soft", "HEAD~1"], consumer)
    shutil.rmtree(folder)
    assert_blocked(process(["git", "commit", "-m", "Leak"], consumer, env))
