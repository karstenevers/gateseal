import json
import os
import shutil
import stat
import subprocess

import pytest
from conftest import TRACKED, commit_hygiene, published_origin
from installation import process, success

pytestmark = pytest.mark.integration


def test_configure_assertions_and_owned_files(installed, configured):
    before = {name: (configured / name).read_bytes() for name in TRACKED}
    assert installed.cli(configured, "configure")["changed"] == []
    installed.cli(configured, "configure", "--capability", "python", code=2)
    assert before == {name: (configured / name).read_bytes() for name in TRACKED}
    path = configured / ".pre-commit-config.yaml"
    path.write_bytes(path.read_bytes() + b" ")
    assert (
        installed.cli(configured, "configure", code=2)["code"] == "ownership_conflict"
    )
    assert installed.cli(configured, "check", code=2)["code"] == "ownership_conflict"
    assert (configured / "gateseal.lock").read_bytes() == before["gateseal.lock"]


def test_configure_recovers_a_lost_lock_and_manifest_edits(installed, configured):
    lock = configured / "gateseal.lock"
    original = lock.read_bytes()
    lock.unlink()
    assert installed.cli(configured, "configure")["changed"] == [lock.name]
    assert lock.read_bytes() == original
    manifest = configured / "gateseal.toml"
    comment = b"# Reviewed hygiene selection\n"
    manifest.write_bytes(comment + manifest.read_bytes())
    mismatch = installed.cli(configured, "check", code=2)
    assert mismatch["code"] == "lock_mismatch"
    assert "gateseal configure" in mismatch["next_steps"]
    assert installed.cli(configured, "configure")["changed"] == [lock.name]
    assert installed.cli(configured, "check")["code"] == "consistent"
    assert manifest.read_bytes().startswith(comment)
    recompiled = lock.read_bytes()
    other_build = json.loads(recompiled)
    other_build["policy"]["digest"] = "sha256:" + "0" * 64
    lock.write_text(json.dumps(other_build))
    refused = installed.cli(configured, "configure", code=2)
    assert refused["code"] == "selected_compiler_required"
    assert json.loads(lock.read_bytes()) == other_build
    lock.write_bytes(recompiled)


@pytest.mark.parametrize(
    "name,mode",
    [
        (".pre-commit-config.yaml", 0o755),
        ("gateseal.toml", 0o755),
        ("gateseal.lock", 0o755),
    ],
)
def test_tracked_file_mode_drift_is_rejected(installed, prepared, name, mode):
    before = {item: (prepared / item).read_bytes() for item in TRACKED}
    path = prepared / name
    path.chmod(mode)
    try:
        for command in ("check", "configure"):
            result = installed.cli(prepared, command, code=2)
            assert result["code"] == "file_mode_mismatch"
            diagnostic = json.dumps(result)
            assert name in diagnostic
            assert "restore" in diagnostic.lower()
            assert stat.S_IMODE(path.stat().st_mode) == mode
            assert before == {item: (prepared / item).read_bytes() for item in TRACKED}
    finally:
        path.chmod(0o644)
    assert installed.cli(prepared, "check")["code"] == "consistent"
    success(
        ["git", "commit", "--allow-empty", "-m", "Restore policy permissions"],
        prepared,
        installed.environment,
    )
    assert before == {item: (prepared / item).read_bytes() for item in TRACKED}


def test_private_and_read_only_policy_inputs_are_accepted(installed, prepared):
    modes = {
        "gateseal.toml": 0o400,
        "gateseal.lock": 0o600,
        ".pre-commit-config.yaml": 0o664,
    }
    before = {name: (prepared / name).read_bytes() for name in modes}
    try:
        for name, mode in modes.items():
            (prepared / name).chmod(mode)
        assert installed.cli(prepared, "check")["code"] == "consistent"
        assert installed.cli(prepared, "configure")["changed"] == []
        for name, mode in modes.items():
            assert stat.S_IMODE((prepared / name).stat().st_mode) == mode
            success(["git", "add", name], prepared)
            staged = success(["git", "ls-files", "--stage", "--", name], prepared)
            assert staged.stdout.startswith("100644 ")
        success(
            ["git", "commit", "--allow-empty", "-m", "Preserve input permissions"],
            prepared,
            installed.environment,
        )
        assert before == {name: (prepared / name).read_bytes() for name in modes}
    finally:
        for name in modes:
            (prepared / name).chmod(0o644)


def test_fifo_policy_file_is_rejected_without_blocking(installed, configured):
    path = configured / ".pre-commit-config.yaml"
    original = path.read_bytes()
    path.unlink()
    os.mkfifo(path)
    try:
        result = subprocess.run(
            [str(installed.command), "--repo", str(configured), "--json", "check"],
            cwd=configured,
            env=installed.environment,
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 2, result.stdout + result.stderr
        document = json.loads(result.stdout)
        assert document["code"] == "unsafe_path"
        assert path.name in json.dumps(document)
        assert stat.S_ISFIFO(path.stat().st_mode)
    finally:
        path.unlink()
        path.write_bytes(original)
        path.chmod(0o644)
    assert installed.cli(configured, "check")["code"] == "consistent"


def test_unmanaged_config_is_preserved(installed, consumer):
    (consumer / ".pre-commit-config.yaml").write_bytes(b"unmanaged\n")
    installed.cli(
        consumer,
        "configure",
        "--capability",
        "hygiene",
        code=2,
    )
    assert not (consumer / "gateseal.lock").exists()
    assert (consumer / ".pre-commit-config.yaml").read_bytes() == b"unmanaged\n"


def checks(result):
    """Return each check's status from a status or doctor result."""
    return {
        entry["check"]: entry["status"]
        for entry in result["diagnostics"]
        if entry["category"] == "check"
    }


def test_a_plain_commit_runs_the_hooks_and_every_check_passes(installed, prepared):
    for command in ("status", "doctor"):
        result = installed.cli(prepared, command)
        assert result["code"] == "healthy"
        assert checks(result) == {
            "lock": "passed",
            "pre-commit": "passed",
            "launcher": "passed",
            "push-launcher": "skipped",
            "mise": "skipped",
        }
        assert result["next_steps"] == []
    assert installed.cli(prepared, "run")["code"] == "checks_passed"
    commit_hygiene(prepared)


def test_a_fresh_clone_commits_without_a_gateseal_command(installed, tmp_path):
    origin = published_origin(installed, tmp_path)
    second = tmp_path / "second ü"
    success(["git", "clone", origin, second], tmp_path)
    success(["git", "config", "user.name", "Second"], second)
    success(["git", "config", "user.email", "second@example.invalid"], second)
    commit_hygiene(second)
    assert installed.cli(second, "status")["code"] == "healthy"


def test_doctor_names_a_missing_launcher_and_warns_about_a_hooks_path(
    installed, adopted
):
    hook = adopted / ".git/hooks/pre-commit"
    hook.unlink()
    missing = installed.cli(adopted, "doctor", code=3)
    assert missing["code"] == "unhealthy"
    assert checks(missing)["launcher"] == "failed"
    assert missing["next_steps"] == ["gateseal setup --refresh"]
    assert installed.cli(adopted, "status")["next_steps"] == missing["next_steps"]
    assert installed.cli(adopted, "setup", "--refresh")["changed"] == [str(hook)]
    assert installed.cli(adopted, "doctor")["code"] == "healthy"
    success(["git", "config", "core.hooksPath", "custom-hooks"], adopted)
    warned = installed.cli(adopted, "doctor")
    assert warned["code"] == "healthy"
    [warning] = [e for e in warned["diagnostics"] if e["severity"] == "warning"]
    assert warning["category"] == "custom_hooks_path"
    assert warned["next_steps"] == ["Remove the core.hooksPath setting."]


def test_pre_commit_preserves_ordinary_partial_staging(installed, prepared):
    path = prepared / "partially-staged.txt"
    path.write_text("staged\n")
    success(["git", "add", path.name], prepared)
    path.write_text("staged\nunstaged\n")
    success(["git", "commit", "-m", "Preserve unstaged source"], prepared)
    assert path.read_text() == "staged\nunstaged\n"
    assert (
        success(["git", "show", "HEAD:partially-staged.txt"], prepared).stdout
        == "staged\n"
    )
    path.write_text("staged\n")


def test_crlf_checkout_is_consistent_and_commits(installed, tmp_path):
    origin = published_origin(installed, tmp_path)
    clone = tmp_path / "crlf clone"
    success(["git", "clone", "-c", "core.autocrlf=true", origin, clone], tmp_path)
    assert b"\r\n" in (clone / ".pre-commit-config.yaml").read_bytes()
    assert installed.cli(clone, "check")["code"] == "consistent"
    assert installed.cli(clone, "configure")["changed"] == []
    assert success(["git", "status", "--porcelain"], clone).stdout == ""
    (clone / "contribution.txt").write_text("crlf contributor\n")
    success(["git", "add", "contribution.txt"], clone)
    success(
        ["git", "-c", "user.name=Crlf", "-c", "user.email=crlf@example.invalid"]
        + ["commit", "-m", "Commit from a CRLF checkout"],
        clone,
    )


def test_nonfinite_lock_number_is_a_json_error(installed, configured):
    lock = configured / "gateseal.lock"
    lock.write_text(
        lock.read_text().replace(
            '"native_artifacts": {}', '"native_artifacts": {"x": 1e9999}'
        )
    )
    assert installed.cli(configured, "check", code=2)["code"] == "invalid_json"


@pytest.mark.parametrize("as_json", [False, True])
def test_verbose_run_retains_complete_native_output(installed, prepared, as_json):
    directory = prepared / "long-native-output"
    directory.mkdir()
    names = [f"{index:03d}-{'x' * 90}.txt" for index in range(100)]
    try:
        for full in (False, True):
            for name in names:
                (directory / name).write_text("trailing space  \n")
            success(["git", "add", directory.name], prepared)
            arguments = [installed.command, "--repo", prepared, "run"]
            if as_json:
                arguments.append("--json")
            if full:
                arguments.append("--verbose")
            result = process(arguments, prepared, installed.environment)
            assert result.returncode == 1, result.stdout + result.stderr
            if full:
                assert "[truncated" not in result.stdout
                assert all(name in result.stdout for name in names)
            else:
                assert "[truncated" in result.stdout
            if as_json:
                assert json.loads(result.stdout)["code"] == "checks_failed"
    finally:
        success(["git", "rm", "--cached", "-rf", directory.name], prepared)
        shutil.rmtree(directory)


def test_commit_output_is_complete(installed, prepared):
    directory = prepared / "long-commit-output"
    directory.mkdir()
    names = [f"{index:03d}-{'x' * 90}.txt" for index in range(100)]
    try:
        for name in names:
            (directory / name).write_text("trailing space  \n")
        success(["git", "add", directory.name], prepared)
        committed = process(["git", "commit", "-m", "Long output"], prepared)
        assert committed.returncode != 0
        output = committed.stdout + committed.stderr
        assert all(name in output for name in names)
        assert "[truncated" not in output
    finally:
        success(["git", "rm", "--cached", "-rfq", directory.name], prepared)
        shutil.rmtree(directory)


def test_outside_git_is_a_clear_error(installed, tmp_path):
    plain = tmp_path / "plain directory"
    plain.mkdir()
    result = installed.cli(plain, "status", code=2)
    assert result["code"] == "not_a_repository"


def test_a_malformed_gateseal_pin_is_structured(installed, configured):
    lock = configured / "gateseal.lock"
    document = json.loads(lock.read_bytes())
    document["gateseal"]["commit"] = "main"
    lock.write_text(json.dumps(document))
    for command in ("check", "configure"):
        result = installed.cli(configured, command, code=2)
        assert result["code"] == "invalid_field"
        assert "gateseal.commit" in result["diagnostics"][0]["message"]
        assert "Restore gateseal.lock from Git" in result["next_steps"][0]
