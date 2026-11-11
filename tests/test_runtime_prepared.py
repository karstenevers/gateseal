"""A published preparation: status, doctor, pinning, publishing and activation.

uv builds a real attempt's environments from downloads, so each test builds a
stand-in attempt. Shell scripts stand in for the prepared interpreter,
compiler, pre-commit, hook executables and Betterleaks, and the record seals
them as activation seals its environments. A uv that reports a release too
old to prepare stops every activation that would build an attempt.
"""

import dataclasses
import os
import platform
import shutil
import stat

import pytest
from conftest import TRACKED, disguise
from gateseal import __version__, bootstrap, interpreter_store, lock, runtime
from gateseal.compiler_release import TARGETS, load_runtime
from gateseal.diagnostics import GatesealError
from installation import clone
from prepared_attempts import (
    BETTERLEAKS,
    PRE_COMMIT,
    answer,
    configure,
    executable,
    prepare,
)

MINIMUM_UV = load_runtime().minimum_uv
HOOK = ".git/hooks/pre-commit"
CURRENT = ".git/gateseal/current"


@pytest.fixture(scope="module")
def templates(tmp_path_factory):
    """Return a configured clone for each capability set, to copy per test."""
    found = {}
    for capabilities in (("hygiene",), ("hygiene", "secrets")):
        directory = tmp_path_factory.mktemp("template")
        root = clone(directory / "origin", directory / "consumer")
        worktree = runtime.locate_worktree(root)
        configure(worktree, capabilities)
        found[capabilities] = root
    return found


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    path = tmp_path / "store"
    monkeypatch.setenv(interpreter_store.STORE_VARIABLE, str(path))
    return path


def first_on_path(monkeypatch, uv, text):
    executable(uv, text)
    monkeypatch.setenv("PATH", os.pathsep.join([str(uv.parent), os.environ["PATH"]]))


@pytest.fixture
def old_uv(tmp_path, monkeypatch):
    """Put a uv first on PATH that reports a release activation refuses."""
    first_on_path(monkeypatch, tmp_path / "old uv" / "uv", answer("uv 0.1.0"))


def configured(templates, tmp_path, capabilities=("hygiene",), folder="consumer"):
    root = tmp_path / folder
    shutil.copytree(templates[capabilities], root, symlinks=True)
    worktree = runtime.locate_worktree(root)
    local = runtime.preparation_directory(worktree, create=True)
    worktree.hook.parent.mkdir(exist_ok=True)
    return worktree, lock.load(worktree.root, local)


def refused(call, *arguments, **options):
    with pytest.raises(GatesealError) as failure:
        call(*arguments, **options)
    return failure.value


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_status_and_doctor_report_a_healthy_preparation(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path, ("hygiene", "secrets"))
    attempt = prepare(worktree, locked)
    notes = lock.describe(locked)
    status = runtime.status(worktree, locked)
    assert (status.command, status.code, status.notes) == ("status", "recorded", notes)
    assert "doctor" in status.message
    assert status.next_steps == lock.review_steps(notes)
    assert runtime.require_prepared(worktree, locked).attempt == attempt
    doctor = runtime.doctor(worktree, locked)
    assert (doctor.command, doctor.code, doctor.notes) == ("doctor", "healthy", notes)
    assert doctor.details == f"gateseal {__version__}\n{PRE_COMMIT}\n"


def test_status_names_the_step_an_unprepared_worktree_needs(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path)
    status = runtime.status(worktree, locked)
    assert status.code == "needs_activation"
    assert status.next_steps == (
        "gateseal activate",
        *lock.review_steps(lock.describe(locked)),
    )


def unhook(worktree, locked):
    worktree.hook.unlink()
    return locked


def plain_hook(worktree, locked):
    worktree.hook.chmod(0o644)
    return locked


def other_root(worktree, locked):
    return dataclasses.replace(locked, root=worktree.root.parent / "elsewhere")


def other_lock(worktree, locked):
    snapshot = dataclasses.replace(locked.snapshot, lock=b"{}")
    return dataclasses.replace(locked, snapshot=snapshot)


def not_an_attempt(worktree, locked):
    (worktree.preparation / bootstrap.CURRENT).unlink()
    (worktree.preparation / bootstrap.CURRENT).symlink_to("elsewhere")
    return locked


def edited_runtime(worktree, locked):
    current = worktree.preparation / bootstrap.CURRENT
    (current / "compiler/bin/python").write_text("#!/bin/sh\necho changed\n")
    return locked


@pytest.mark.parametrize(
    "change,named",
    [
        (unhook, None),
        (plain_hook, None),
        (other_root, "{root}"),
        (other_lock, None),
        (not_an_attempt, None),
        (edited_runtime, None),
    ],
)
def test_a_preparation_that_cannot_run_needs_activation(
    templates, tmp_path, change, named
):
    worktree, locked = configured(templates, tmp_path)
    prepare(worktree, locked)
    error = refused(runtime.require_prepared, worktree, change(worktree, locked))
    assert (error.code, error.exit_code, error.next_step) == (
        "needs_activation",
        3,
        "gateseal activate",
    )
    assert named is None or named.format(root=worktree.root) in str(error)


def test_a_hook_gateseal_does_not_own_is_refused(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path)
    prepare(worktree, locked)
    executable(worktree.hook, "#!/bin/sh\nexit 0\n")
    assert refused(runtime.require_prepared, worktree, locked).code == "unmanaged_hook"


def test_doctor_reads_every_byte_that_status_trusts_by_size_and_time(
    templates, tmp_path
):
    worktree, locked = configured(templates, tmp_path)
    attempt = prepare(worktree, locked)
    disguise(attempt / "compiler/bin/python", "#!/bin/sh\necho 9\n")
    assert runtime.status(worktree, locked).code == "recorded"
    error = refused(runtime.doctor, worktree, locked)
    assert error.code == "needs_activation"


def test_doctor_stops_on_a_release_binary_that_reports_another_version(
    templates, tmp_path
):
    worktree, locked = configured(templates, tmp_path, ("hygiene", "secrets"))
    prepare(worktree, locked, betterleaks=BETTERLEAKS.format(version="1.8.0"))
    error = refused(runtime.doctor, worktree, locked)
    assert (error.code, error.exit_code) == ("tool_failed", 4)
    assert all(version in str(error) for version in ("1.8.0", "1.9.0"))


def test_a_pinned_attempt_outlives_cleanup_until_released(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path)
    attempt = prepare(worktree, locked)
    stale = prepare(worktree, locked, publish=False)
    clean = (worktree.preparation, worktree.root, worktree.git)
    with runtime.pinned(worktree, locked) as record:
        assert record.attempt == attempt
        runtime._clean_up(*clean)
        assert stale.is_dir()
    runtime._clean_up(*clean)
    assert not stale.exists()
    assert attempt.is_dir()


def test_pinning_an_unprepared_worktree_needs_activation(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path)
    shutil.rmtree(worktree.preparation)
    with pytest.raises(GatesealError) as failure, runtime.pinned(worktree, locked):
        pass
    assert failure.value.code == "needs_activation"
    assert mode(worktree.preparation) == 0o700


def test_a_commit_runs_pre_commit_from_the_attempt_in_the_worktree(
    templates, tmp_path, monkeypatch
):
    worktree, locked = configured(templates, tmp_path)
    prepare(worktree, locked)
    record = runtime.require_prepared(worktree, locked)
    monkeypatch.chdir(tmp_path)
    started = []

    def execve(path, arguments, environment):
        started.append((path, arguments, environment, os.getcwd()))
        raise SystemExit(0)

    monkeypatch.setattr(os, "execve", execve)
    with pytest.raises(SystemExit):
        runtime.exec_pre_commit(record, locked.selection.tools)
    [(path, arguments, environment, cwd)] = started
    python = str(record.paths.pre_commit_python)
    assert (path, cwd) == (python, str(worktree.root))
    assert arguments == [python, "-I", "-B", "-m", "pre_commit", "run"] + [
        "--config",
        bootstrap.CONFIG,
        "--hook-stage",
        "pre-commit",
    ]
    assert environment["PRE_COMMIT_HOME"] == str(record.paths.pre_commit_home)


def test_publishing_installs_the_hook_once_and_moves_current(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path)
    first = prepare(worktree, locked, publish=False)
    assert runtime._publish(worktree, locked, first) == (HOOK, CURRENT)
    assert worktree.hook.read_bytes() == bootstrap.HOOK
    assert mode(worktree.hook) == 0o755
    second = prepare(worktree, locked, publish=False)
    assert runtime._publish(worktree, locked, second) == (CURRENT,)
    assert os.readlink(worktree.preparation / bootstrap.CURRENT) == second.name
    assert not list(worktree.preparation.glob(".gateseal-*"))
    worktree.hook.chmod(0o644)
    assert runtime._publish(worktree, locked, first) == (HOOK, CURRENT)
    assert mode(worktree.hook) == 0o755


def test_publishing_stops_when_the_tracked_policy_changed(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path)
    attempt = prepare(worktree, locked, publish=False)
    for name in TRACKED:
        (worktree.root / name).unlink()
    configure(worktree, ("hygiene", "secrets"))
    error = refused(runtime._publish, worktree, locked, attempt)
    assert error.code == "concurrent_change"
    assert not (worktree.preparation / bootstrap.CURRENT).is_symlink()


def test_publishing_leaves_a_hook_gateseal_does_not_own(templates, tmp_path):
    worktree, locked = configured(templates, tmp_path)
    attempt = prepare(worktree, locked, publish=False)
    executable(worktree.hook, "#!/bin/sh\nexit 0\n")
    assert refused(runtime._publish, worktree, locked, attempt).code == (
        "unmanaged_hook"
    )
    assert worktree.hook.read_text() == "#!/bin/sh\nexit 0\n"
    assert not (worktree.preparation / bootstrap.CURRENT).is_symlink()


def orphan(store, tmp_path):
    """Add a store entry whose attempt is gone, as a deleted clone leaves one."""
    gone = tmp_path / ("attempt-" + "1" * 32)
    gone.mkdir()
    name = "ab" * 32

    def install(entry, descriptor):
        executable(entry / "bin" / "python")

    with interpreter_store.sealed(name, {"key": "orphan"}, gone, install):
        pass
    gone.rmdir()
    return store / name


def test_activating_a_healthy_preparation_only_cleans_up(
    templates, tmp_path, store, old_uv
):
    worktree, locked = configured(templates, tmp_path)
    local = worktree.preparation
    first = prepare(worktree, locked)
    second = prepare(worktree, locked, replaces=first.name)
    stale = prepare(worktree, locked, publish=False)
    entry = orphan(store, tmp_path)
    result = runtime.activate(worktree, locked, None)
    assert (result.command, result.code, result.changed, result.next_steps) == (
        "activate",
        "already_prepared",
        (),
        (),
    )
    assert first.is_dir() and second.is_dir()
    assert not stale.exists()
    assert not entry.exists()
    patches = local / runtime.PATCHES
    patches.mkdir()
    for name in ("attempt-b-patch2", "attempt-a-patch1"):
        (patches / name).write_text("diff\n")
    (patches / "folder").mkdir()
    (patches / "link").symlink_to(patches / "attempt-a-patch1")
    steps = runtime.activate(worktree, locked, None).next_steps
    kept = (patches / "attempt-a-patch1", patches / "attempt-b-patch2")
    assert len(steps) == len(kept)
    for step, path in zip(steps, kept, strict=True):
        assert f"git apply --whitespace=nowarn {path}" in step


def test_activating_replaces_a_current_that_is_a_directory(
    templates, tmp_path, monkeypatch
):
    worktree, locked = configured(templates, tmp_path)
    current = worktree.preparation / bootstrap.CURRENT
    current.mkdir()
    (current / "copied").write_text("a copy that dereferenced the link\n")
    built = []

    def stand_in(plan, local):
        built.append(prepare(worktree, locked, publish=False))
        return built[0]

    monkeypatch.setattr(runtime, "_plan", lambda *arguments: None)
    monkeypatch.setattr(runtime, "_prepare", stand_in)
    result = runtime.activate(worktree, locked, None)
    assert result.code == "prepared"
    [attempt] = built
    assert current.is_symlink() and os.readlink(current) == attempt.name


def disguised(worktree, locked):
    attempt = prepare(worktree, locked)
    disguise(attempt / "compiler/bin/python", "#!/bin/sh\necho 9\n")


def failing_probe(worktree, locked):
    prepare(worktree, locked, probe="#!/bin/sh\nexit 1\n")


def unlinked(worktree, locked):
    prepare(worktree, locked, linked=False)


def unprepared(worktree, locked):
    shutil.rmtree(worktree.preparation)
    shutil.rmtree(worktree.hook.parent)


@pytest.mark.parametrize("state", [disguised, failing_probe, unlinked, unprepared])
def test_activating_anything_but_a_healthy_preparation_prepares(
    templates, tmp_path, old_uv, state
):
    worktree, locked = configured(templates, tmp_path)
    state(worktree, locked)
    error = refused(runtime.activate, worktree, locked, None)
    assert (error.code, error.exit_code) == ("uv_version_mismatch", 4)
    assert all(value in str(error) for value in (__version__, MINIMUM_UV))
    assert mode(worktree.preparation) == 0o700
    assert worktree.hook.parent.is_dir()


def test_an_activation_that_cannot_download_python_leaves_no_attempt(
    templates, tmp_path, store, monkeypatch
):
    """The uv on PATH answers, but its CPython install fails as it does offline."""
    worktree, locked = configured(templates, tmp_path)
    first_on_path(
        monkeypatch,
        tmp_path / "offline uv" / "uv",
        '#!/bin/sh\nif [ "$1 $2" = "python install" ]; then\n'
        '  echo "error sending request for url (https://example.invalid)" >&2\n'
        f'  exit 2\nfi\nexec "{shutil.which("uv")}" "$@"\n',
    )
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv cache"))
    error = refused(runtime.activate, worktree, locked, None)
    target = TARGETS[platform.system(), platform.machine()]
    url = interpreter_store.Archive(load_runtime().interpreter(target)).url
    assert error.code == "tool_failed"
    assert url in error.next_step
    assert "gateseal activate" in error.next_step
    assert not list(worktree.preparation.glob("attempt-*"))
    assert [path.name for path in store.iterdir()] == [interpreter_store.LOCK]


def test_activation_checks_the_override_wheel_first(templates, tmp_path, old_uv):
    worktree, locked = configured(templates, tmp_path)
    wheel = tmp_path / f"gateseal-{__version__}-py3-none-any.whl"
    error = refused(runtime.activate, worktree, locked, str(wheel))
    assert error.code == "missing_artifact"
    assert str(wheel) in str(error)


def test_activation_refuses_a_git_directory_the_hook_cannot_pass(
    templates, tmp_path, old_uv
):
    worktree, locked = configured(templates, tmp_path, folder="a=b")
    assert refused(runtime.activate, worktree, locked, None).code == "unsupported_path"


@pytest.mark.parametrize("folder", ["$store", "st:ore"])
def test_activation_refuses_a_store_the_launchers_cannot_name(
    templates, tmp_path, old_uv, monkeypatch, folder
):
    worktree, locked = configured(templates, tmp_path)
    monkeypatch.setenv(interpreter_store.STORE_VARIABLE, str(tmp_path / folder))
    error = refused(runtime.activate, worktree, locked, None)
    assert error.code == "unsupported_path"
    assert str(tmp_path / folder) in str(error)


def test_activation_refuses_a_hook_gateseal_does_not_own(templates, tmp_path, old_uv):
    worktree, locked = configured(templates, tmp_path)
    executable(worktree.hook, "#!/bin/sh\nexit 0\n")
    assert refused(runtime.activate, worktree, locked, None).code == "unmanaged_hook"
