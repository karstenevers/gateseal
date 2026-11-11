import gzip
import json
import shutil
from pathlib import Path

import pytest
from conftest import (
    TRACKED,
    attempts,
    commit_hygiene,
    interrupt_activation,
    uv_shim,
)
from installation import process, success

pytestmark = pytest.mark.integration


def test_unmanaged_hook_is_preserved(installed, configured):
    hook = configured / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    activation = installed.cli(configured, "activate", code=2)
    status = installed.cli(configured, "status")
    assert status["code"] == activation["code"] == "unmanaged_hook"
    assert status["next_steps"] == activation["next_steps"]
    assert hook.read_text() == "#!/bin/sh\nexit 0\n"
    assert not (configured / ".git/gateseal/current").is_symlink()


def test_custom_hook_path_is_not_changed(installed, configured, tmp_path):
    included = tmp_path / "included hooks config"
    success(["git", "config", "include.path", included], configured)
    success(
        ["git", "config", "--file", included, "core.hooksPath", "custom-hooks"],
        configured,
    )
    status = installed.cli(configured, "status")
    activation = installed.cli(configured, "activate", code=2)
    assert status["code"] == activation["code"] == "custom_hooks_path"
    assert status["next_steps"] == activation["next_steps"]
    for result in (status, activation):
        assert result["changed"] == []
        assert (
            "commits can run without gateseal checks"
            in result["diagnostics"][0]["message"]
        )
        (step,) = result["next_steps"]
        assert "git config --show-origin --show-scope --get-all core.hooksPath" in step
        assert "git config --file <path> --unset-all core.hooksPath" in step
        assert "command/environment override" in step
        assert step.endswith("then run gateseal activate.")
    assert (
        success(["git", "config", "--get", "core.hooksPath"], configured).stdout.strip()
        == "custom-hooks"
    )
    assert not (configured / ".git/gateseal/current").is_symlink()
    reported = success(
        ["git", "config", "--show-origin", "--show-scope", "--get-all"]
        + ["core.hooksPath"],
        configured,
    ).stdout.strip()
    scope, origin, value = reported.split("\t")
    assert (scope, origin, value) == ("local", f"file:{included}", "custom-hooks")
    path = origin.removeprefix("file:")
    success(
        ["git", "config", "--file", path, "--unset-all", "core.hooksPath"], configured
    )
    status = installed.cli(configured, "status")
    assert status["code"] == "needs_activation"
    assert status["next_steps"] == ["gateseal activate"]


@pytest.mark.parametrize("explicit_repo", [False, True])
def test_custom_hook_path_recovery_names_the_affected_worktree(
    installed, configured, tmp_path, explicit_repo
):
    success(["git", "config", "core.hooksPath", "custom-hooks"], configured)
    caller = tmp_path / "outside" if explicit_repo else configured / "subdirectory"
    caller.mkdir()
    arguments = ["--repo", configured] if explicit_repo else []
    results = []
    for command, expected in (("status", 0), ("activate", 2)):
        result = process(
            [installed.command, *arguments, command, "--json"],
            caller,
            installed.environment,
        )
        assert result.returncode == expected, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["code"] == "custom_hooks_path"
        (step,) = report["next_steps"]
        assert step.startswith(f"Change your working directory to {configured}. ")
        results.append(step)
    assert results[0] == results[1]
    reported = success(
        [
            "git",
            "config",
            "--show-origin",
            "--show-scope",
            "--get-all",
            "core.hooksPath",
        ],
        configured,
    ).stdout.strip()
    scope, origin, value = reported.split("\t")
    assert (scope, origin, value) == ("local", "file:.git/config", "custom-hooks")
    success(
        [
            "git",
            "config",
            "--file",
            origin.removeprefix("file:"),
            "--unset-all",
            "core.hooksPath",
        ],
        configured,
    )
    assert installed.cli(configured, "status")["code"] == "needs_activation"


def test_custom_hook_path_recovery_restores_commit_checks(installed, adopted):
    success(["git", "config", "core.hooksPath", "custom-hooks"], adopted)
    assert installed.cli(adopted, "status")["code"] == "custom_hooks_path"
    unchecked = success(
        ["git", "commit", "--allow-empty", "-m", "Unchecked commit"], adopted
    )
    assert "trim trailing whitespace" not in unchecked.stderr
    success(["git", "config", "--local", "--unset-all", "core.hooksPath"], adopted)
    installed.cli(adopted, "activate")
    assert installed.cli(adopted, "status")["code"] == "recorded"
    assert installed.cli(adopted, "doctor")["code"] == "healthy"
    commit_hygiene(adopted, installed.environment)


def test_configured_hook_stops_activation_until_turned_off(
    installed, adopted, tmp_path
):
    settings = tmp_path / "global.gitconfig"
    settings.write_text('[hook "lint"]\n\tevent = pre-commit\n\tcommand = true\n')
    env = dict(installed.environment, GIT_CONFIG_GLOBAL=str(settings))
    activation = installed.cli(adopted, "activate", code=2, env=env)
    status = installed.cli(adopted, "status", env=env)
    doctor = installed.cli(adopted, "doctor", code=2, env=env)
    run = installed.cli(adopted, "run", code=2, env=env)
    for result in (activation, status, doctor, run):
        assert result["code"] == "configured_hooks"
    assert activation["changed"] == []
    assert status["next_steps"] == activation["next_steps"]
    (step,) = activation["next_steps"]
    command = step.split(" with ", 1)[1].split(", then run gateseal activate.")[0]
    assert command == "git config --local --replace-all hook.lint.enabled false"
    success(["sh", "-c", command], adopted, env)
    installed.cli(adopted, "activate", env=env)
    assert installed.cli(adopted, "status", env=env)["code"] == "recorded"
    assert installed.cli(adopted, "doctor", env=env)["code"] == "healthy"


@pytest.mark.parametrize(
    "name", ["a=b", "it's", 'say "hi"', "$HOME", "`id`", "a\\b", "a:b"]
)
def test_clone_path_the_hook_cannot_pass_is_refused(installed, configured, name):
    moved = configured.with_name(f"clone {name}")
    configured.rename(moved)
    result = installed.cli(moved, "activate", code=2)
    assert result["code"] == "unsupported_path"
    assert result["diagnostics"][0]["message"].startswith(
        f"gateseal prepares in {moved / '.git'}, whose path contains "
    )
    assert result["next_steps"][0].startswith("Move the clone to a path without")
    assert not (moved / ".git/hooks/pre-commit").exists()
    assert not (moved / ".git/gateseal/current").is_symlink()


def test_interrupted_first_activation_then_failed_retry_names_activate(
    installed, configured, tmp_path
):
    success(["git", "add", *TRACKED], configured)
    local = configured / ".git/gateseal"
    interrupt_activation(installed, configured, "record")
    (partial,) = attempts(configured)
    assert not (local / partial / "prepared.json").exists()
    assert not (configured / ".git/hooks/pre-commit").exists()
    interrupt_activation(installed, configured, "publish")
    (complete,) = attempts(configured)
    assert complete != partial
    assert (local / complete / "prepared.json").is_file()
    assert (configured / ".git/hooks/pre-commit").is_file()
    assert not (local / "current").is_symlink()
    failing = uv_shim(installed, tmp_path, "exit 7")
    for retry in (False, True):
        if retry:
            failed = installed.cli(configured, "activate", code=4, env=failing)
            assert failed["code"] == "tool_failed"
            assert attempts(configured) == set()
            assert not list(local.glob(".gateseal-*"))
        committed = process(["git", "commit", "-m", "Unprepared"], configured)
        assert committed.returncode != 0
        assert "gateseal: This worktree has no usable preparation." in committed.stderr
        assert "gateseal activate" in committed.stderr
        assert "env:" not in committed.stderr
        assert "No such file" not in committed.stderr
    assert installed.cli(configured, "activate")["code"] == "prepared"
    success(["git", "commit", "-m", "Prepared"], configured)


def test_patch_in_a_deleted_attempt_is_kept(installed, configured):
    installed.cli(configured, "activate")
    local = configured / ".git/gateseal"
    (old,) = attempts(configured)
    (local / old / "pre-commit-home" / "patch1-1").write_bytes(b"unstaged edits\n")
    (local / "current").unlink()
    result = installed.cli(configured, "activate")
    assert result["code"] == "prepared"
    assert old not in attempts(configured)
    kept = local / "patches" / f"{old}-patch1-1"
    assert kept.read_bytes() == b"unstaged edits\n"
    step = (
        f"pre-commit saved unstaged edits in {kept} that the worktree does not "
        f"match. If a stopped commit lost them, run "
        f"git apply --whitespace=nowarn {kept}. "
        f"Then delete {kept}."
    )
    assert result["next_steps"] == [step]
    assert installed.cli(configured, "activate")["next_steps"] == [step]
    kept.unlink()
    assert installed.cli(configured, "activate")["next_steps"] == []


def test_python_archive_is_reused_and_verified(installed, configured, tmp_path):
    installed.cli(configured, "activate")
    cache = Path(installed.environment["UV_CACHE_DIR"]) / "gateseal-python-archives"
    (archive,) = cache.iterdir()
    current = configured / ".git/gateseal/current"
    offline = uv_shim(installed, tmp_path, 'exec "{uv}" python install --offline "$@"')
    current.unlink()
    assert installed.cli(configured, "activate", env=offline)["code"] == "prepared"
    assert list(cache.iterdir()) == [archive]
    tampered = tmp_path / "tampered archives"
    tampered.mkdir()
    (tampered / archive.name).write_bytes(
        gzip.compress(gzip.decompress(archive.read_bytes()), mtime=0)
    )
    current.unlink()
    failed = installed.cli(
        configured,
        "activate",
        code=4,
        env=dict(
            offline,
            UV_PYTHON_CACHE_DIR=str(tampered),
            GATESEAL_INTERPRETER_STORE=str(tmp_path / "tampered store"),
        ),
    )
    assert "Hash mismatch" in json.dumps(failed)
    assert failed["next_steps"] == [
        "uv's cached Python archive does not match its pinned SHA-256. "
        f"Delete {tampered}, then run gateseal activate."
    ]


def test_local_override_preserves_tracked_source(installed, configured, tmp_path):
    duplicate = tmp_path / installed.wheel.name
    shutil.copyfile(installed.wheel, duplicate)
    before = (configured / "gateseal.lock").read_bytes()
    installed.cli(configured, "activate", "--compiler-wheel", str(duplicate))
    assert (configured / "gateseal.lock").read_bytes() == before
    again = installed.cli(configured, "activate", "--compiler-wheel", str(duplicate))
    assert again["code"] == "already_prepared"
    assert again["changed"] == []
    assert len(list((configured / ".git/gateseal").glob("attempt-*"))) == 1
    duplicate.write_bytes(b"wrong wheel")
    assert (
        installed.cli(
            configured, "activate", "--compiler-wheel", str(duplicate), code=4
        )["code"]
        == "artifact_hash_mismatch"
    )
    assert (configured / "gateseal.lock").read_bytes() == before


def test_symlinked_external_paths(installed, consumer, tmp_path):
    real = tmp_path / "real wheels"
    real.mkdir()
    shutil.copyfile(installed.wheel, real / installed.wheel.name)
    (tmp_path / "linked wheels").symlink_to(real, target_is_directory=True)
    linked = tmp_path / "linked wheels" / installed.wheel.name
    renamed = real.resolve() / "renamed.whl"
    shutil.copyfile(installed.wheel, renamed)
    alias = tmp_path / installed.wheel.name
    alias.symlink_to(renamed)
    flags = ("--compiler-wheel", linked, "--compiler-sha256", installed.checksum)
    aliased = ("--compiler-wheel", alias, "--compiler-sha256", installed.checksum)
    refused = installed.cli(
        consumer, "configure", "--capability", "hygiene", *aliased, code=2
    )
    assert refused["code"] == "invalid_field"
    assert refused["diagnostics"][0]["message"] == (
        f"Local wheel {renamed} must be an absolute path to a file named "
        f"{installed.wheel.name}."
    )
    installed.cli(consumer, "configure", "--capability", "hygiene", *flags)
    source = json.loads((consumer / "gateseal.lock").read_bytes())["compiler"]
    assert source["source"]["location"] == str(real.resolve() / installed.wheel.name)
    assert installed.cli(consumer, "activate", *flags[:2])["code"] == "prepared"
    refused = installed.cli(consumer, "activate", *aliased[:2], code=2)
    assert refused["code"] == "invalid_artifact"
    assert refused["diagnostics"][0]["message"] == (
        f"Compiler wheel resolves to {renamed}, which is not named "
        f"{installed.wheel.name}."
    )
    environment = tmp_path / "linked command environment"
    environment.symlink_to(installed.python.parents[1], target_is_directory=True)
    result = process(
        [environment / "bin" / "python", "-I", "-B", "-m", "gateseal"]
        + ["--repo", consumer, "--json", "check"],
        consumer,
        installed.environment,
    )
    assert json.loads(result.stdout)["code"] == "consistent", result.stdout
