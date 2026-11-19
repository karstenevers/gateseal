"""gateseal status and gateseal doctor report the same five checks.

The pre-commit that gateseal setup installs needs a download, so a stand-in
answers for it. The clone, its launcher, the Lock and the mise on the PATH are
real files, and mise is a shell script that prints a version.
"""

import contextlib
import io
import json
import os
import shutil

import pytest
from conftest import git_path
from gateseal import health, lock, setup
from gateseal.cli import main
from gateseal.worktree import locate_worktree
from installation import success
from stand_ins import configure, executable

PYTHON = "/owned/pre-commit/bin/python"
LAUNCHER = "#!/bin/sh\nexit 0\n"
REFRESH = ["gateseal setup --refresh"]
INSTALL_MISE = ["Install mise 2026.10.19 or later."]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine where gateseal setup ran; returns the folder of its PATH's mise."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    executable(setup.Layout(setup.data_root()).launcher, LAUNCHER)
    monkeypatch.setattr(setup, "require_pre_commit", lambda: PYTHON)
    shims = tmp_path / "shims"
    shims.mkdir()
    (shims / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", os.pathsep.join([str(shims), "/usr/bin", "/bin"]))
    for name in health.AUTO_INSTALL:
        monkeypatch.delenv(name, raising=False)
    return shims


def clone(directory, *capabilities):
    """Return a configured clone that holds the launcher of the machine."""
    root = directory / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], directory)
    configure(locate_worktree(root), capabilities)
    success(["git", "add", "-A"], root)
    executable(git_path(root, "hooks/pre-commit"), LAUNCHER)
    return root


@pytest.fixture
def hygiene(machine, tmp_path):
    return clone(tmp_path, "hygiene")


@pytest.fixture
def secrets(machine, tmp_path):
    executable(machine / "mise", "#!/bin/sh\necho 2026.10.19 linux-x64\n")
    return clone(tmp_path, "hygiene", "secrets")


def gateseal(root, command):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), "--json", command])
    return code, json.loads(output.getvalue())


def check(result, name):
    """Return the severity, status and message of one check."""
    [entry] = [e for e in result["diagnostics"] if e.get("check") == name]
    return entry["severity"], entry["status"], entry["message"]


def warnings(result):
    return [e for e in result["diagnostics"] if e["severity"] == "warning"]


@pytest.mark.parametrize("command", ["status", "doctor"])
def test_a_set_up_clone_is_healthy(hygiene, command):
    code, result = gateseal(hygiene, command)
    assert (code, result["code"]) == (0, "healthy")
    assert result["diagnostics"][0]["message"] == "Every check passed."
    hook = git_path(hygiene, "hooks/pre-commit")
    assert check(result, "lock")[:2] == ("info", "passed")
    assert check(result, "pre-commit") == (
        "info",
        "passed",
        f"The pre-commit of {PYTHON} starts.",
    )
    assert check(result, "launcher") == (
        "info",
        "passed",
        f"{hook} is the commit launcher.",
    )
    assert check(result, "mise") == (
        "info",
        "skipped",
        "No selected tool is a release binary.",
    )
    assert result["next_steps"] == []


def remove(hook):
    hook.unlink()


def replace(hook):
    hook.write_text("#!/bin/sh\nexit 1\n")


def lock_out(hook):
    hook.chmod(0o644)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (remove, "is missing, so git commit runs no hook."),
        (replace, "is not the commit launcher that gateseal setup installs."),
        (lock_out, "is not executable, so git commit skips it."),
    ],
)
def test_a_launcher_that_git_would_not_run_fails_its_check(hygiene, change, problem):
    hook = git_path(hygiene, "hooks/pre-commit")
    change(hook)
    code, result = gateseal(hygiene, "status")
    assert (code, result["code"]) == (0, "unhealthy")
    assert result["diagnostics"][0]["message"] == "1 of 5 checks failed."
    assert check(result, "launcher") == ("error", "failed", f"{hook} {problem}")
    assert result["next_steps"] == REFRESH
    assert gateseal(hygiene, "doctor")[0] == 3


def test_a_clone_is_unhealthy_before_setup_wrote_its_launcher(hygiene):
    setup.Layout(setup.data_root()).launcher.unlink()
    hook = git_path(hygiene, "hooks/pre-commit")
    _, result = gateseal(hygiene, "status")
    assert check(result, "launcher")[2] == (
        f"{hook} is not the commit launcher that gateseal setup installs."
    )


def test_mise_passes_for_a_release_binary(secrets, machine):
    for command in ("status", "doctor"):
        code, result = gateseal(secrets, command)
        assert (code, result["code"]) == (0, "healthy")
        assert check(result, "mise") == (
            "info",
            "passed",
            f"{machine / 'mise'} is mise 2026.10.19 or later.",
        )


NEED = "A release binary starts through mise 2026.10.19 or later, and"


@pytest.mark.parametrize(
    ("mise", "message"),
    [
        ("#!/bin/sh\necho 2026.10.18 linux-x64\n", f"{NEED} an older one is on PATH."),
        (None, f"{NEED} none is on PATH."),
        (
            "#!/bin/sh\necho broken >&2\nexit 1\n",
            "The mise on PATH did not report its version: broken",
        ),
    ],
    ids=["old", "missing", "broken"],
)
def test_an_old_missing_or_broken_mise_fails_its_check(secrets, machine, mise, message):
    (machine / "mise").unlink()
    if mise:
        executable(machine / "mise", mise)
    code, result = gateseal(secrets, "doctor")
    assert (code, result["code"]) == (3, "unhealthy")
    assert check(result, "mise") == ("error", "failed", message)
    assert result["next_steps"] == INSTALL_MISE


def test_mise_is_not_checked_while_the_lock_does_not_load(
    secrets, machine, monkeypatch
):
    (machine / "mise").unlink()
    monkeypatch.setenv("MISE_AUTO_INSTALL", "0")
    (secrets / "gateseal.lock").unlink()
    code, result = gateseal(secrets, "doctor")
    assert (code, result["code"]) == (3, "unhealthy")
    assert check(result, "lock")[:2] == ("error", "failed")
    assert check(result, "mise") == (
        "info",
        "skipped",
        "The Lock did not load, so its tools are unknown.",
    )
    assert warnings(result) == []


@pytest.mark.parametrize(
    ("values", "named"),
    [
        ({"MISE_AUTO_INSTALL": "0"}, "MISE_AUTO_INSTALL"),
        ({"MISE_EXEC_AUTO_INSTALL": "false"}, "MISE_EXEC_AUTO_INSTALL"),
        ({"MISE_AUTO_INSTALL": "No"}, "MISE_AUTO_INSTALL"),
        ({"MISE_EXEC_AUTO_INSTALL": " FALSE "}, "MISE_EXEC_AUTO_INSTALL"),
        (
            {"MISE_AUTO_INSTALL": "false", "MISE_EXEC_AUTO_INSTALL": "0"},
            "MISE_AUTO_INSTALL and MISE_EXEC_AUTO_INSTALL",
        ),
    ],
)
def test_doctor_warns_when_mise_would_not_install(secrets, monkeypatch, values, named):
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    code, result = gateseal(secrets, "doctor")
    assert (code, result["code"]) == (0, "healthy")
    [warning] = warnings(result)
    assert warning["category"] == "mise_auto_install_off"
    assert warning["message"] == (
        f"mise's install at first use is off through {named}, so a hook starts an "
        "executable of the same name from the PATH when mise has not installed "
        "its tool."
    )
    assert result["next_steps"] == [f"Unset {named}."]
    assert warnings(gateseal(secrets, "status")[1]) == []


def test_doctor_does_not_warn_while_mise_installs(secrets, monkeypatch):
    assert warnings(gateseal(secrets, "doctor")[1]) == []
    monkeypatch.setenv("MISE_AUTO_INSTALL", "1")
    monkeypatch.setenv("MISE_EXEC_AUTO_INSTALL", "true")
    assert warnings(gateseal(secrets, "doctor")[1]) == []
    monkeypatch.setenv("MISE_AUTO_INSTALL", "yes")
    assert warnings(gateseal(secrets, "doctor")[1]) == []


def test_doctor_does_not_warn_without_a_release_binary(hygiene, monkeypatch):
    monkeypatch.setenv("MISE_AUTO_INSTALL", "0")
    code, result = gateseal(hygiene, "doctor")
    assert (code, warnings(result)) == (0, [])


# The launcher that setup wrote names its hook type in the tests below, as
# pre-commit's does, so a commit launcher in the push launcher's place is found.
COMMIT = "#!/bin/sh\nexec pre-commit hook-impl --hook-type=pre-commit\n"
PUSH = "#!/bin/sh\nexec pre-commit hook-impl --hook-type=pre-push\n"
SLOW = (
    '\n[hooks.slow-tests]\nname = "Run the slow tests"\n'
    'run = "slow-tests.sh"\nstages = ["push", "ci"]\n'
)


@pytest.fixture
def pushing(machine, tmp_path):
    """A clone with a push hook and both launchers; returns it and its push hook."""
    executable(setup.Layout(setup.data_root()).launcher, COMMIT)
    root = clone(tmp_path, "hygiene")
    executable(root / "slow-tests.sh")
    with (root / "gateseal.toml").open("a") as manifest:
        manifest.write(SLOW)
    worktree = locate_worktree(root)
    lock.configure(
        root, worktree.git, worktree.preparation, None, lock.PolicyRequest(), None
    )
    executable(git_path(root, "hooks/pre-commit"), COMMIT)
    return root, executable(git_path(root, "hooks/pre-push"), PUSH)


@pytest.mark.parametrize("command", ["status", "doctor"])
def test_a_clone_with_a_push_hook_and_its_launcher_is_healthy(pushing, command):
    root, hook = pushing
    code, result = gateseal(root, command)
    assert (code, result["code"]) == (0, "healthy")
    assert check(result, "push-launcher") == (
        "info",
        "passed",
        f"{hook} is the push launcher.",
    )


def take_the_commit_launcher(hook):
    hook.write_text(COMMIT)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (remove, "is missing, so git push runs no hook."),
        (
            take_the_commit_launcher,
            "is not the push launcher that gateseal setup installs.",
        ),
        (lock_out, "is not executable, so git push skips it."),
    ],
)
def test_a_push_launcher_that_git_would_not_run_fails_its_check(
    pushing, change, problem
):
    root, hook = pushing
    change(hook)
    code, result = gateseal(root, "status")
    assert (code, result["code"]) == (0, "unhealthy")
    assert result["diagnostics"][0]["message"] == "1 of 5 checks failed."
    assert check(result, "launcher")[1] == "passed"
    assert check(result, "push-launcher") == ("error", "failed", f"{hook} {problem}")
    assert result["next_steps"] == REFRESH
    assert gateseal(root, "doctor")[0] == 3


def test_a_push_launcher_is_unknown_before_setup_wrote_its_launcher(pushing):
    root, hook = pushing
    setup.Layout(setup.data_root()).launcher.unlink()
    assert check(gateseal(root, "status")[1], "push-launcher")[2] == (
        f"{hook} is not the push launcher that gateseal setup installs."
    )


def test_no_push_launcher_is_needed_without_a_push_hook(machine, tmp_path):
    """Another tool's push hook, such as Git LFS's, is then no concern."""
    root = clone(tmp_path, "hygiene")
    executable(git_path(root, "hooks/pre-push"), "#!/bin/sh\ngit lfs pre-push\n")
    for command in ("status", "doctor"):
        code, result = gateseal(root, command)
        assert (code, result["code"]) == (0, "healthy")
        assert check(result, "push-launcher") == (
            "info",
            "skipped",
            "No selected hook runs at a push.",
        )


def test_a_push_launcher_that_runs_no_check_is_reported(machine, tmp_path):
    """It keeps Git LFS from writing its push hook, and no check guards the push."""
    executable(setup.Layout(setup.data_root()).launcher, COMMIT)
    root = clone(tmp_path, "hygiene")
    executable(git_path(root, "hooks/pre-commit"), COMMIT)
    hook = executable(git_path(root, "hooks/pre-push"), PUSH)
    for command in ("status", "doctor"):
        code, result = gateseal(root, command)
        assert (code, result["code"]) == (0, "healthy")
        assert check(result, "push-launcher") == (
            "info",
            "skipped",
            f"No selected hook runs at a push, so the push launcher {hook} runs no "
            "check. gateseal setup --refresh removes it, so that Git LFS can write "
            "its push hook.",
        )
    identity = ["-c", "user.name=gateseal test", "-c", "user.email=t@x.invalid"]
    commit = ["commit", "--quiet", "--no-verify", "-m", "Configure"]
    success(["git", *identity, *commit], root)
    success(["git", "worktree", "add", "--quiet", tmp_path / "linked"], root)
    (tmp_path / "linked" / ".pre-commit-config.yaml").write_text(
        '{"default_install_hook_types": ["pre-commit", "pre-push"], "repos": []}'
    )
    assert check(gateseal(root, "status")[1], "push-launcher")[2] == (
        "No selected hook runs at a push."
    )
    (tmp_path / "linked" / ".pre-commit-config.yaml").unlink()
    setup.Layout(setup.data_root()).launcher.unlink()
    assert check(gateseal(root, "status")[1], "push-launcher")[2] == (
        "No selected hook runs at a push."
    )


def test_the_push_launcher_is_not_checked_while_the_lock_does_not_load(pushing):
    root, hook = pushing
    hook.unlink()
    (root / "gateseal.lock").unlink()
    code, result = gateseal(root, "doctor")
    assert (code, result["code"]) == (3, "unhealthy")
    assert result["diagnostics"][0]["message"] == "1 of 5 checks failed."
    assert check(result, "push-launcher") == (
        "info",
        "skipped",
        "The Lock did not load, so its hooks are unknown.",
    )


def hooks_path_warning(root):
    success(["git", "config", "core.hooksPath", "custom-hooks"], root)
    [warning] = warnings(gateseal(root, "doctor")[1])
    return warning["message"]


def test_doctor_warns_that_a_hooks_path_skips_the_commit_launcher(hygiene):
    assert hooks_path_warning(hygiene) == (
        "Git's core.hooksPath names custom-hooks, so git commit skips the commit "
        "launcher."
    )


def test_doctor_warns_that_a_hooks_path_skips_the_push_launcher_too(pushing):
    root, _ = pushing
    assert hooks_path_warning(root) == (
        "Git's core.hooksPath names custom-hooks, so git commit and git push skip "
        "their launchers."
    )


def test_doctor_warns_about_a_hooks_path_while_the_lock_does_not_load(pushing):
    root, _ = pushing
    (root / "gateseal.lock").unlink()
    assert hooks_path_warning(root) == (
        "Git's core.hooksPath names custom-hooks, so git commit skips the commit "
        "launcher."
    )
