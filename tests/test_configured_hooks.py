import pytest
from gateseal import runtime
from gateseal.diagnostics import GatesealError
from gateseal.runtime import locate_worktree
from installation import success


@pytest.fixture
def repository(tmp_path, monkeypatch):
    settings = tmp_path / "global.gitconfig"
    settings.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(settings))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("GIT_CONFIG_COUNT", raising=False)
    monkeypatch.delenv("GIT_CONFIG_PARAMETERS", raising=False)
    root = tmp_path / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], tmp_path)
    return root


def configure(root, scope, name, event="pre-commit", command="true"):
    success(["git", "config", scope, f"hook.{name}.event", event], root)
    if command is not None:
        success(["git", "config", scope, f"hook.{name}.command", command], root)


def refused(root):
    with pytest.raises(GatesealError) as failure:
        runtime.require_owned_hook(locate_worktree(root))
    return failure.value


def turn_off(error, root):
    """Run the commands the next step names, as a user pastes them."""
    named = error.next_step.split(" with ", 1)[1]
    command = named.split(", then run gateseal activate.", 1)[0]
    success(["sh", "-c", command], root)
    return command


def test_a_global_hook_is_refused_until_turned_off_as_named(repository):
    configure(repository, "--global", "lint")
    error = refused(repository)
    assert error.code == "configured_hooks"
    assert error.exit_code == 2
    assert "lint (global)" in str(error)
    assert error.next_step.startswith(
        f"Change your working directory to {repository}. "
    )
    assert (
        turn_off(error, repository)
        == "git config --local --replace-all hook.lint.enabled false"
    )
    runtime.require_owned_hook(locate_worktree(repository))


def test_hooks_from_every_scope_are_named(repository):
    configure(repository, "--global", "lint")
    configure(repository, "--local", "loc")
    error = refused(repository)
    assert error.code == "configured_hooks"
    assert "lint (global)" in str(error) and "loc (local)" in str(error)
    assert turn_off(error, repository) == (
        "git config --local --replace-all hook.lint.enabled false && "
        "git config --local --replace-all hook.loc.enabled false"
    )
    runtime.require_owned_hook(locate_worktree(repository))


def test_an_enable_flag_in_a_later_scope_is_found_as_the_step_says(repository):
    success(["git", "config", "extensions.worktreeConfig", "true"], repository)
    configure(repository, "--global", "lint")
    success(["git", "config", "--worktree", "hook.lint.enabled", "true"], repository)
    error = refused(repository)
    assert "git config --show-scope --get-all hook.<name>.enabled" in error.next_step
    turn_off(error, repository)
    assert refused(repository).code == "configured_hooks"
    listed = ["git", "config", "--show-scope", "--get-all", "hook.lint.enabled"]
    assert success(listed, repository).stdout.split() == [
        "local",
        "false",
        "worktree",
        "true",
    ]
    success(["git", "config", "--worktree", "hook.lint.enabled", "false"], repository)
    runtime.require_owned_hook(locate_worktree(repository))


def test_a_hook_name_stays_copy_pasteable(repository):
    configure(repository, "--local", "it's")
    turn_off(refused(repository), repository)
    runtime.require_owned_hook(locate_worktree(repository))


def test_every_local_enable_flag_is_replaced(repository):
    configure(repository, "--local", "lint")
    for value in ("false", "true"):
        success(["git", "config", "--add", "hook.lint.enabled", value], repository)
    turn_off(refused(repository), repository)
    runtime.require_owned_hook(locate_worktree(repository))


@pytest.mark.parametrize("name", [b"disabled\tlint", b"disabled\t\xff"])
def test_a_hook_named_like_a_disabled_one_is_refused(
    repository, tmp_path, monkeypatch, name
):
    configure(repository, "--global", "lint")
    success(["git", "config", "--global", "hook.lint.enabled", "false"], repository)
    settings = tmp_path / "global.gitconfig"
    crafted = b'[hook "' + name + b'"]\n\tevent = pre-commit\n\tcommand = true\n'
    settings.write_bytes(settings.read_bytes() + crafted)
    unrelated = tmp_path / "unrelated.gitconfig"
    unrelated.touch()
    monkeypatch.setenv("GIT_CONFIG", str(unrelated))
    error = refused(repository)
    assert error.code == "configured_hooks"
    assert "disabled\t" in str(error)
    assert "git config --show-scope --show-origin --name-only --list" in error.next_step
    settings.write_bytes(settings.read_bytes().removesuffix(crafted))
    runtime.require_owned_hook(locate_worktree(repository))


def test_a_global_hooks_path_is_refused_whatever_git_config_names(
    repository, tmp_path, monkeypatch
):
    success(["git", "config", "--global", "core.hooksPath", "elsewhere"], repository)
    unrelated = tmp_path / "unrelated.gitconfig"
    unrelated.touch()
    monkeypatch.setenv("GIT_CONFIG", str(unrelated))
    assert refused(repository).code == "custom_hooks_path"


def test_hooks_for_other_events_pass(repository):
    runtime.require_owned_hook(locate_worktree(repository))
    configure(repository, "--global", "message", event="commit-msg")
    runtime.require_owned_hook(locate_worktree(repository))


def test_a_broken_hook_configuration_carries_gits_message(repository):
    configure(repository, "--local", "broken", command=None)
    error = refused(repository)
    assert error.code == "git_configuration_failed"
    assert error.exit_code == 4
    assert "hook.broken.command" in str(error)
