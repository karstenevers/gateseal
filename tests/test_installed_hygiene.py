import importlib.util
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from conftest import (
    TRACKED,
    attempts,
    disguise,
    interrupt_activation,
    overwrite,
    prepared_state,
    preserved,
    published,
    published_origin,
    recorded_tree,
    remove_file,
    uv_shim,
)
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
    assert installed.cli(configured, "configure", code=2)["code"] == "missing_artifact"
    flags = (
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
    )
    assert installed.cli(configured, "configure", *flags)["changed"] == [lock.name]
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
        if mode & 0o111:
            success(["git", "add", name], prepared)
            staged = success(["git", "ls-files", "--stage", "--", name], prepared)
            assert staged.stdout.startswith("100755 ")
            committed = process(
                ["git", "commit", "--allow-empty", "-m", "Reject executable policy"],
                prepared,
                installed.environment,
            )
            assert committed.returncode != 0
            assert name in committed.stderr
            assert "unsupported staged type" in committed.stderr
    finally:
        path.chmod(0o644)
        success(["git", "add", name], prepared)
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
        "--compiler-wheel",
        str(installed.wheel),
        "--compiler-sha256",
        installed.checksum,
        code=2,
    )
    assert not (consumer / "gateseal.lock").exists()
    assert (consumer / ".pre-commit-config.yaml").read_bytes() == b"unmanaged\n"


def test_real_commit_manual_run_and_minimal_path(installed, prepared):
    minimal = dict(
        installed.environment,
        PATH="/usr/bin:/bin",
        PYTHONPATH="/nonexistent",
        UV_PYTHON="/nonexistent",
        UV_PROJECT="/nonexistent",
    )
    assert installed.cli(prepared, "doctor")["code"] == "healthy"
    assert installed.cli(prepared, "run")["code"] == "checks_passed"
    healthy = installed.cli(prepared, "activate", env=minimal)
    assert healthy["code"] == "already_prepared"
    assert healthy["changed"] == []
    bad = prepared / "hygiene.txt"
    bad.write_bytes(b"trailing space  \n")
    success(["git", "add", "hygiene.txt"], prepared)
    failure = process(
        ["/usr/bin/git", "commit", "-m", "Bad hygiene"], prepared, minimal
    )
    assert failure.returncode != 0
    assert bad.read_bytes() == b"trailing space\n"
    success(["git", "add", "hygiene.txt"], prepared)
    success(["/usr/bin/git", "commit", "-m", "Good hygiene"], prepared, minimal)
    assert installed.cli(prepared, "status")["code"] == "recorded"


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


def test_unstaged_native_policy_cannot_change_checks(installed, prepared):
    config = prepared / ".pre-commit-config.yaml"
    original = config.read_bytes()
    config.write_text('{"repos": []}\n')
    try:
        result = process(
            ["git", "commit", "--allow-empty", "-m", "Cannot bypass checks"], prepared
        )
        assert result.returncode != 0
        assert "Unstaged policy edits" in result.stderr
    finally:
        config.write_bytes(original)


CHECK = "Run gateseal check, fix what it reports, stage the files, then retry."


@pytest.mark.parametrize(
    ("change", "expected", "next_step"),
    [
        (
            "missing",
            "Missing from the commit's index: gateseal.lock.",
            "Stage gateseal.toml, gateseal.lock and .pre-commit-config.yaml "
            "together. On a branch without gateseal, merge or rebase a branch "
            "that has them, or commit with --no-verify.",
        ),
        (
            "malformed",
            "Staged gateseal.lock is not a valid Lock: Expected UTF-8 JSON.",
            CHECK,
        ),
        ("unsupported", "Staged gateseal.lock has an unsupported schema.", CHECK),
        ("boolean", "Staged gateseal.lock has an unsupported schema.", CHECK),
        ("duplicate", "is not a valid Lock: Duplicate JSON key 'schema'.", CHECK),
        (
            "symlink",
            "gateseal.lock has an unsupported staged type 120000.",
            "Stage gateseal.lock as a regular, non-executable file, then retry.",
        ),
        (
            "conflicted",
            "gateseal.lock is conflicted in the index.",
            "Resolve the conflict in gateseal.lock, stage it, then retry.",
        ),
        (
            "gitlink",
            "gateseal.lock has an unsupported staged type 160000.",
            "Stage gateseal.lock as a regular, non-executable file, then retry.",
        ),
        (
            "unreadable",
            "Git could not read staged gateseal.lock: ",
            "Stage gateseal.lock again with git add, then retry.",
        ),
        (
            "alternate missing",
            "Missing from the commit's index: gateseal.lock.",
            "Stage gateseal.toml, gateseal.lock and .pre-commit-config.yaml "
            "together. On a branch without gateseal, merge or rebase a branch "
            "that has them, or commit with --no-verify.",
        ),
    ],
)
def test_bad_staged_lock_stops_in_the_gate(
    installed, prepared, tmp_path, change, expected, next_step
):
    lock = prepared / "gateseal.lock"
    original = lock.read_bytes()
    env = None
    try:
        if change == "missing":
            success(["git", "rm", "--cached", "gateseal.lock"], prepared)
        elif change == "alternate missing":
            env = dict(os.environ, GIT_INDEX_FILE=str(tmp_path / "alternate index"))
            success(["git", "read-tree", "HEAD"], prepared, env)
            success(["git", "rm", "-q", "--cached", "gateseal.lock"], prepared, env)
        elif change == "gitlink":
            head = success(["git", "rev-parse", "HEAD"], prepared).stdout.strip()
            success(
                ["git", "update-index", "--cacheinfo", f"160000,{head},gateseal.lock"],
                prepared,
            )
        elif change == "unreadable":
            lock.write_bytes(b'{"schema":1,"unreadable":true}\n')
            success(["git", "add", "gateseal.lock"], prepared)
            oid = success(["git", "hash-object", "gateseal.lock"], prepared).stdout
            (prepared / ".git/objects" / oid[:2] / oid[2:].strip()).unlink()
        elif change == "conflicted":
            oid = success(
                ["git", "hash-object", "-w", "gateseal.lock"], prepared
            ).stdout.strip()
            success(
                ["git", "update-index", "--force-remove", "gateseal.lock"], prepared
            )
            result = subprocess.run(
                ["git", "update-index", "--index-info"],
                cwd=prepared,
                input="".join(f"100644 {oid} {n}\tgateseal.lock\n" for n in (1, 2, 3)),
                text=True,
                capture_output=True,
            )
            assert result.returncode == 0
        elif change == "symlink":
            lock.unlink()
            lock.symlink_to("gateseal.toml")
            success(["git", "add", "gateseal.lock"], prepared)
        else:
            replacements = {
                "malformed": b"{",
                "unsupported": b'{"schema":2}',
                "boolean": b'{"schema":true}',
                "duplicate": b'{"schema":1,"schema":1}',
            }
            lock.write_bytes(replacements[change])
            success(["git", "add", "gateseal.lock"], prepared)
        result = process([prepared / ".git/hooks/pre-commit"], prepared, env)
        assert result.returncode == 3, result.stdout + result.stderr
        assert result.stderr.startswith("gateseal: ")
        assert expected in result.stderr
        assert result.stderr.endswith(f"\nNext: {next_step}\n")
        assert "Traceback" not in result.stderr
    finally:
        if lock.is_symlink():
            lock.unlink()
        lock.write_bytes(original)
        success(["git", "add", "gateseal.lock"], prepared)


def test_runtime_seals_cover_stdlib_and_extra_imports(installed, prepared, tmp_path):
    state = prepared_state(prepared)
    compiler = Path(state["paths"]["compiler_python"])
    site = Path(
        success(
            [
                compiler,
                "-I",
                "-B",
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            prepared,
        ).stdout.strip()
    )
    extra = site / "unexpected.py"
    extra.write_text("value = 1\n")
    try:
        assert installed.cli(prepared, "doctor", code=3)["code"] == "needs_activation"
        result = process([prepared / ".git/hooks/pre-commit"], prepared)
        assert result.returncode == 3
    finally:
        extra.unlink()
    interpreter = recorded_tree(state, "interpreter")
    source = next(interpreter.glob("*/lib/python3.14/fractions.py"))
    with preserved(source) as original:
        overwrite(source, original + b"\n")
        installed.cli(prepared, "run", code=3)
    unused = next(interpreter.glob("*/lib/python3.14/this.py"))
    with preserved(unused) as original:
        overwrite(unused, original.replace(b"Gur", b"Tur", 1))
        assert process([prepared / ".git/hooks/pre-commit"], prepared).returncode == 0
        assert installed.cli(prepared, "doctor", code=3)["code"] == "needs_activation"
    library = next(
        path for path in interpreter.glob("*/lib/libpython3.14.*") if path.is_file()
    )
    with preserved(library) as original:
        overwrite(library, original + b"changed")
        installed.cli(prepared, "doctor", code=3)
    assert installed.cli(prepared, "doctor")["code"] == "healthy"


def test_status_does_not_execute_prepared_tools(installed, prepared, tmp_path):
    record = published(prepared) / "prepared.json"
    entry = published(prepared) / "hook" / "python"
    original, target = record.read_bytes(), os.readlink(entry)
    state = json.loads(original)
    sentinel = tmp_path / "unexpected-execution"
    fake = tmp_path / "prepared-tool"
    fake.write_text(f"#!/bin/sh\nprintf called > {str(sentinel)!r}\nexit 91\n")
    fake.chmod(0o755)
    for name in ("interpreter", "compiler_python", "pre_commit_python"):
        state["paths"][name] = str(fake)
    record.write_text(json.dumps(state))
    entry.unlink()
    entry.symlink_to(fake)
    try:
        assert installed.cli(prepared, "status")["code"] == "recorded"
        assert not sentinel.exists()
    finally:
        record.write_bytes(original)
        entry.unlink()
        entry.symlink_to(target)


def test_hook_python_must_be_the_recorded_interpreter(installed, prepared):
    entry = published(prepared) / "hook" / "python"
    target = os.readlink(entry)
    entry.unlink()
    entry.symlink_to(installed.python.resolve())
    try:
        status = installed.cli(prepared, "status")
        assert status["code"] == "needs_activation"
        assert f"Prepared entry point {entry} changed." in json.dumps(
            status, ensure_ascii=False
        )
        committed = process(
            ["git", "commit", "--allow-empty", "-m", "Other interpreter"], prepared
        )
        assert committed.returncode != 0
        assert f"gateseal: Prepared entry point {entry} changed." in committed.stderr
    finally:
        entry.unlink()
        entry.symlink_to(target)
    assert installed.cli(prepared, "status")["code"] == "recorded"


def test_prepared_record_round_trips_byte_for_byte(prepared):
    from gateseal.bootstrap import PreparedRecord
    from gateseal.validation import canonical

    attempt = published(prepared)
    document = PreparedRecord.read(attempt).document()
    assert canonical(document) == (attempt / "prepared.json").read_bytes()


def test_cache_loss_is_observable_and_activation_repairs(installed, prepared):
    lock = (prepared / "gateseal.lock").read_bytes()
    state = prepared_state(prepared)
    environment = Path(next(iter(state["hook_environments"])))
    for directory, _, _ in os.walk(environment.parent):
        Path(directory).chmod(0o700)
    shutil.rmtree(environment)
    assert installed.cli(prepared, "doctor", code=3)["code"] == "needs_activation"
    assert process([prepared / ".git/hooks/pre-commit"], prepared).returncode == 3
    native_env = dict(
        installed.environment,
        PRE_COMMIT_HOME=state["paths"]["pre_commit_home"],
        PIP_NO_INDEX="1",
        UV_OFFLINE="1",
        VIRTUALENV_DOWNLOAD="0",
    )
    native = process(
        [
            state["paths"]["pre_commit_python"],
            "-I",
            "-B",
            "-m",
            "pre_commit",
            "run",
            "--all-files",
        ],
        prepared,
        native_env,
    )
    assert native.returncode != 0
    assert "Installing environment" in native.stdout + native.stderr
    assert "No matching distribution" in native.stdout + native.stderr
    installed.cli(prepared, "activate")
    assert (prepared / "gateseal.lock").read_bytes() == lock
    success(["git", "commit", "--allow-empty", "-m", "Repaired cache"], prepared)


@pytest.mark.parametrize(
    ("module", "damage"),
    [("pre_commit_hooks.check_yaml", "missing"), ("ruamel.yaml", "corrupt")],
)
def test_hook_import_damage_is_observable_and_activation_repairs(
    installed, prepared, module, damage
):
    state = prepared_state(prepared)
    environment, record = next(iter(state["hook_environments"].items()))
    python = Path(record["python"])
    marker = Path(environment) / ".install_state_v1"
    marker_bytes = marker.read_bytes()
    source = Path(
        success(
            [
                python,
                "-I",
                "-B",
                "-c",
                f"import {module}; print({module}.__file__)",
            ],
            prepared,
        ).stdout.strip()
    )
    before = {name: (prepared / name).read_bytes() for name in TRACKED}
    try:
        if damage == "missing":
            remove_file(source)
        else:
            # Python trusts bytecode whose source keeps its size and time,
            # so damage that commits cannot see must reach the bytecode.
            cached = Path(importlib.util.cache_from_source(str(source)))
            data = cached.read_bytes()
            overwrite(cached, data[:16] + bytes(len(data) - 16))
        assert python.is_file()
        assert marker.read_bytes() == marker_bytes
        sample = prepared / f"hook-health-{damage}.yaml"
        sample.write_text("valid: true\n")
        success(["git", "add", sample.name], prepared)
        doctor = installed.cli(prepared, "doctor", code=3)
        assert doctor["code"] == "needs_activation"
        assert "gateseal activate" in doctor["next_steps"]
        installed.cli(prepared, "run", code=3 if damage == "missing" else 1)
        failed_commit = process(
            ["git", "commit", "--allow-empty", "-m", "Damaged hook imports"], prepared
        )
        assert failed_commit.returncode != 0
        assert installed.cli(prepared, "activate")["code"] == "prepared"
        assert installed.cli(prepared, "doctor")["code"] == "healthy"
        assert installed.cli(prepared, "run")["code"] == "checks_passed"
        success(
            ["git", "commit", "--allow-empty", "-m", "Repair hook imports"], prepared
        )
        assert before == {name: (prepared / name).read_bytes() for name in TRACKED}
    finally:
        installed.cli(prepared, "activate")


@pytest.mark.parametrize(
    ("entry", "damage"),
    [
        ("trailing-whitespace-fixer", "missing"),
        ("end-of-file-fixer", "corrupt"),
        ("check-yaml", "not-executable"),
    ],
)
def test_hook_executable_damage_is_observable_and_activation_repairs(
    installed, prepared, entry, damage
):
    state_path = published(prepared) / "prepared.json"
    state_bytes = state_path.read_bytes()
    state = json.loads(state_bytes)
    environment, record = next(iter(state["hook_environments"].items()))
    python = Path(record["python"])
    marker = Path(environment) / ".install_state_v1"
    marker_bytes = marker.read_bytes()
    script = Path(environment) / "bin" / entry
    mode = script.stat().st_mode
    before = {name: (prepared / name).read_bytes() for name in TRACKED}
    try:
        if damage == "missing":
            remove_file(script)
        elif damage == "corrupt":
            disguise(script, "#!/bin/sh\nexit 97\n")
        else:
            script.chmod(mode & ~0o111)
        assert python.is_file()
        assert marker.read_bytes() == marker_bytes
        success(
            [
                python,
                "-I",
                "-B",
                "-c",
                "from pre_commit_hooks.trailing_whitespace_fixer import main; "
                "from pre_commit_hooks.end_of_file_fixer import main; "
                "from pre_commit_hooks.check_yaml import main; import ruamel.yaml",
            ],
            prepared,
        )
        sample = prepared / f"hook-executable-{entry}-{damage}.yaml"
        sample.write_text("valid: true\n")
        success(["git", "add", sample.name], prepared)
        # Commits compare the hook entry's metadata, so only the same-size
        # corrupt script reaches pre-commit; doctor compares its content too.
        corrupt = damage == "corrupt"
        doctor = installed.cli(prepared, "doctor", code=3)
        assert doctor["code"] == "needs_activation"
        assert "gateseal activate" in doctor["next_steps"]
        assert state_path.read_bytes() == state_bytes
        installed.cli(prepared, "run", code=1 if corrupt else 3)
        failed_commit = process(
            ["git", "commit", "-m", "Damaged hook executable"], prepared
        )
        assert failed_commit.returncode != 0
        assert installed.cli(prepared, "activate")["code"] == "prepared"
        assert installed.cli(prepared, "doctor")["code"] == "healthy"
        assert installed.cli(prepared, "activate")["code"] == "already_prepared"
        assert published(prepared) != state_path.parent
        assert attempts(prepared) == {
            published(prepared).name,
            prepared_state(prepared)["replaces"],
        }
        assert installed.cli(prepared, "run")["code"] == "checks_passed"
        success(["git", "commit", "-m", "Repair hook executable"], prepared)
        assert before == {name: (prepared / name).read_bytes() for name in TRACKED}
    finally:
        installed.cli(prepared, "activate")


def test_interrupted_activation_keeps_the_published_preparation(
    installed, prepared, tmp_path
):
    state = prepared_state(prepared)
    before = published(prepared).name
    kept = {before} | ({state["replaces"]} - {None})
    compiler = recorded_tree(state, "compiler_python")
    installer = next(compiler.glob("lib/python3.14/site-packages/gateseal-*/INSTALLER"))
    with preserved(installer) as original:
        overwrite(installer, original.upper())
        interrupt_activation(installed, prepared, "publish")
        assert published(prepared).name == before
        success(["git", "commit", "--allow-empty", "-m", "Interrupted"], prepared)
        failing = uv_shim(installed, tmp_path, "exit 7")
        failed = installed.cli(prepared, "activate", code=4, env=failing)
        assert failed["code"] == "tool_failed"
        assert published(prepared).name == before
        assert attempts(prepared) == kept
        success(["git", "commit", "--allow-empty", "-m", "Failed retry"], prepared)
        assert installed.cli(prepared, "activate")["code"] == "prepared"
    after = published(prepared).name
    assert after != before
    assert attempts(prepared) == {after, before}
    assert prepared_state(prepared)["replaces"] == before
    success(["git", "commit", "--allow-empty", "-m", "Reprepared"], prepared)


def test_cleanup_never_follows_symlinks(installed, prepared, tmp_path):
    local = prepared / ".git/gateseal"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")
    kept = attempts(prepared)
    linked = local / ("attempt-" + "a" * 32)
    linked.symlink_to(outside, target_is_directory=True)
    escaping = local / ("attempt-" + "b" * 32)
    escaping.mkdir()
    (escaping / "escape").symlink_to(outside / "keep.txt")
    (local / ".gateseal-stray").write_text("stray\n")
    assert installed.cli(prepared, "activate")["code"] == "already_prepared"
    assert attempts(prepared) == kept
    assert not linked.is_symlink()
    assert not escaping.exists()
    assert not (local / ".gateseal-stray").exists()
    assert (outside / "keep.txt").read_text() == "keep\n"


def test_dereferenced_copy_of_current_names_activate(installed, prepared):
    current = prepared / ".git/gateseal/current"
    attempt = published(prepared)
    current.unlink()
    shutil.copytree(attempt, current, symlinks=False)
    assert not (current / "hook/python").is_symlink()
    committed = process(
        ["git", "commit", "--allow-empty", "-m", "Dereferenced"], prepared
    )
    assert committed.returncode != 0
    assert "gateseal: This worktree has no usable preparation." in committed.stderr
    assert "Next: Run gateseal activate, then retry." in committed.stderr
    assert "env:" not in committed.stderr
    assert "Traceback" not in committed.stderr
    assert installed.cli(prepared, "activate")["code"] == "prepared"
    assert current.is_symlink()
    success(["git", "commit", "--allow-empty", "-m", "Reactivated"], prepared)


def test_second_contributor_clone_activates_with_newer_uv_and_commits_without_uv(
    installed, tmp_path
):
    origin = published_origin(installed, tmp_path)
    second = tmp_path / "second"
    clone = 'umask 002 && exec git clone "$0" "$1"'
    success(["sh", "-c", clone, origin, second], tmp_path)
    assert stat.S_IMODE((second / ".pre-commit-config.yaml").stat().st_mode) == 0o664
    assert installed.cli(second, "status")["code"] == "needs_activation"
    assert installed.cli(second, "check")["code"] == "consistent"
    shims = tmp_path / "uv shims"
    shims.mkdir()
    uv = shims / "uv"
    path = os.pathsep.join([str(shims), installed.environment["PATH"]])
    shimmed = dict(installed.environment, PATH=path)
    uv.write_text('#!/bin/sh\necho "uv 0.13.4"\n')
    uv.chmod(0o755)
    older = installed.cli(second, "activate", code=4, env=shimmed)
    assert older["code"] == "uv_version_mismatch"
    real = shutil.which("uv", path=installed.environment["PATH"])
    uv.write_text(
        '#!/bin/sh\nif [ "$1" = --version ]; then echo "uv 0.99.0"; exit 0; fi\n'
        f'exec "{real}" "$@"\n'
    )
    assert installed.cli(second, "activate", env=shimmed)["code"] == "prepared"
    shutil.rmtree(shims)
    (second / "contribution.txt").write_text("second contributor\n")
    success(["git", "add", "contribution.txt"], second)
    success(
        ["git", "-c", "user.name=Second", "-c", "user.email=second@example.invalid"]
        + ["commit", "-m", "Second contribution"],
        second,
    )
    assert installed.cli(second, "status")["code"] == "recorded"


def test_crlf_checkout_is_consistent_and_commits(installed, tmp_path):
    origin = published_origin(installed, tmp_path)
    clone = tmp_path / "crlf clone"
    success(["git", "clone", "-c", "core.autocrlf=true", origin, clone], tmp_path)
    assert b"\r\n" in (clone / ".pre-commit-config.yaml").read_bytes()
    assert installed.cli(clone, "check")["code"] == "consistent"
    assert installed.cli(clone, "configure")["changed"] == []
    assert installed.cli(clone, "activate")["code"] == "prepared"
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


@pytest.mark.parametrize("damage", ["malformed", "missing", "dangling", "edited"])
def test_hook_ownership_is_the_hook_bytes(installed, prepared, tmp_path, damage):
    current = prepared / ".git/gateseal/current"
    hook = prepared / ".git/hooks/pre-commit"
    original_hook = hook.read_bytes()
    if damage == "malformed":
        (published(prepared) / "prepared.json").write_bytes(b"{")
    elif damage == "missing":
        current.unlink()
    elif damage == "dangling":
        current.unlink()
        current.symlink_to("attempt-" + "0" * 32)
    else:
        hook.write_bytes(original_hook + b"# local edit\n")
    if damage == "edited":
        result = installed.cli(prepared, "activate", code=2)
        assert result["code"] == "unmanaged_hook"
        diagnostic = json.dumps(result, ensure_ascii=False)
        for text in (str(hook), "Preserve", "move it out", "gateseal activate"):
            assert text in diagnostic
        assert hook.read_bytes() == original_hook + b"# local edit\n"
        hook.rename(tmp_path / "preserved-pre-commit")
    else:
        assert installed.cli(prepared, "status")["code"] == "needs_activation"
        assert installed.cli(prepared, "doctor", code=3)["code"] == "needs_activation"
        committed = process(
            ["git", "commit", "--allow-empty", "-m", "Damaged preparation"], prepared
        )
        assert committed.returncode != 0
        assert "gateseal activate" in committed.stderr
    assert installed.cli(prepared, "activate")["code"] == "prepared"
    assert hook.read_bytes() == original_hook
    assert installed.cli(prepared, "doctor")["code"] == "healthy"


def test_copied_clone_stops_and_leaves_the_original_untouched(
    installed, prepared, tmp_path
):
    original_file = prepared / "original-staged.txt"
    original_file.write_bytes(b"original  \n")
    success(["git", "add", original_file.name], prepared)
    status = success(["git", "status", "--porcelain"], prepared).stdout
    before = (published(prepared).name, attempts(prepared))
    copy = tmp_path.resolve() / "copied clone"
    try:
        success(["cp", "-R", prepared, copy], tmp_path)
        copied_file = copy / "copy-staged.txt"
        copied_file.write_bytes(b"copy  \n")
        success(["git", "add", copied_file.name], copy)
        committed = process(["git", "commit", "-m", "From the copy"], copy)
        assert committed.returncode != 0
        assert f"gateseal: This preparation belongs to {prepared}." in committed.stderr
        assert f"Next: Run gateseal activate in {copy}." in committed.stderr
        assert copied_file.read_bytes() == b"copy  \n"
        assert original_file.read_bytes() == b"original  \n"
        assert success(["git", "status", "--porcelain"], prepared).stdout == status
        assert (published(prepared).name, attempts(prepared)) == before
        stale = installed.cli(copy, "status")
        assert stale["code"] == "needs_activation"
        assert f"This preparation belongs to {prepared}." in json.dumps(
            stale, ensure_ascii=False
        )
        linked = tmp_path.resolve() / "copied worktree"
        success(["git", "worktree", "add", "-q", "-b", "copied", linked], copy)
        (linked / "linked.txt").write_text("linked\n")
        success(["git", "add", "linked.txt"], linked)
        committed = process(["git", "commit", "-m", "From the worktree"], linked)
        assert committed.returncode != 0
        assert "gateseal: This worktree has no usable preparation." in committed.stderr
        assert installed.cli(linked, "activate")["code"] == "prepared"
        success(["git", "commit", "-m", "From the worktree"], linked)
        success(["git", "worktree", "remove", "--force", linked], copy)
        assert installed.cli(copy, "activate")["code"] == "prepared"
        assert process(["git", "commit", "-m", "Fixed copy"], copy).returncode != 0
        assert copied_file.read_bytes() == b"copy\n"
        assert (copy / original_file.name).read_bytes() == b"original\n"
        success(["git", "add", copied_file.name, original_file.name], copy)
        success(["git", "commit", "-m", "From the activated copy"], copy)
        assert original_file.read_bytes() == b"original  \n"
        assert (published(prepared).name, attempts(prepared)) == before
    finally:
        success(["git", "rm", "--cached", "-q", original_file.name], prepared)
        original_file.unlink()


def test_hooks_path_set_after_activation_is_reported(installed, prepared):
    success(["git", "config", "core.hooksPath", "custom-hooks"], prepared)
    try:
        status = installed.cli(prepared, "status")
        run = installed.cli(prepared, "run", code=2)
    finally:
        success(["git", "config", "--unset", "core.hooksPath"], prepared)
    assert "core.hooksPath" in status["diagnostics"][0]["message"]
    assert run["code"] == "custom_hooks_path"


def test_commit_without_gateseal_files_names_the_remedy(installed, prepared):
    branch = success(["git", "branch", "--show-current"], prepared).stdout.strip()
    success(["git", "switch", "-q", "--orphan", "without-gateseal"], prepared)
    sample = prepared / "orphan.txt"
    try:
        sample.write_text("orphan\n")
        success(["git", "add", sample.name], prepared)
        committed = process(["git", "commit", "-m", "No gateseal"], prepared)
        assert committed.returncode != 0
        assert (
            "Missing from the commit's index: "
            "gateseal.toml, gateseal.lock, .pre-commit-config.yaml."
        ) in committed.stderr
        assert "merge or rebase" in committed.stderr
        assert "--no-verify" in committed.stderr
        assert "gateseal activate" not in committed.stderr
        success(["git", "commit", "--no-verify", "-m", "No gateseal"], prepared)
    finally:
        success(["git", "switch", "-q", "-f", branch], prepared)
        success(["git", "branch", "-q", "-D", "without-gateseal"], prepared)
        sample.unlink(missing_ok=True)


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


def test_pre_commit_is_gits_direct_child(installed, prepared, tmp_path):
    environment = Path(next(iter(prepared_state(prepared)["hook_environments"])))
    script = environment / "bin" / "trailing-whitespace-fixer"
    observed = tmp_path / "grandparent"
    sample = prepared / "process-tree.txt"
    with preserved(script):
        disguise(
            script,
            "#!/bin/sh\n"
            'grandparent=$(ps -o ppid= -p "$PPID")\n'
            f"ps -o comm= -p $grandparent > {str(observed)!r}\n",
        )
        sample.write_text("process tree\n")
        success(["git", "add", sample.name], prepared)
        success(["git", "commit", "-m", "Observe the process tree"], prepared)
    assert Path(observed.read_text().strip()).name == "git"


def test_outside_git_is_a_clear_error(installed, tmp_path):
    plain = tmp_path / "plain directory"
    plain.mkdir()
    result = installed.cli(plain, "status", code=2)
    assert result["code"] == "not_a_repository"
    assert "gateseal activate" not in json.dumps(result)


def test_malformed_lock_url_is_structured(installed, configured):
    url = (
        "https://[x/example/wheels/"
        + "0" * 40
        + "/artifacts/gateseal-0.1.0-py3-none-any.whl"
    )
    lock = configured / "gateseal.lock"
    document = json.loads(lock.read_bytes())
    document["compiler"]["source"] = {"kind": "https-wheel", "location": url}
    lock.write_text(json.dumps(document))
    assert installed.cli(configured, "check", code=2)["code"] == "invalid_field"
    lock.unlink()
    flags = ("--compiler-source", url, "--compiler-sha256", installed.checksum)
    assert installed.cli(configured, "configure", *flags, code=2)["code"] == (
        "invalid_field"
    )


def test_run_reports_an_interrupted_pre_commit(installed, prepared):
    environment = Path(next(iter(prepared_state(prepared)["hook_environments"])))
    script = environment / "bin" / "trailing-whitespace-fixer"
    with preserved(script):
        disguise(script, '#!/bin/sh\nkill -TERM "$PPID"\n')
        result = installed.cli(prepared, "run", code=4)
    assert result["code"] == "tool_failed"
    assert result["next_steps"] == ["gateseal doctor"]
