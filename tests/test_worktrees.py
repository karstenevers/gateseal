import os

import pytest
from conftest import TRACKED, commit_hygiene, git_path, on_machine, published_origin
from installation import process, success

pytestmark = pytest.mark.integration

MINIMAL_PATH = {"PATH": "/usr/bin:/bin", "PYTHONPATH": "/nonexistent"}


def add_worktree(clone, path, branch):
    success(["git", "worktree", "add", "-q", "-b", branch, path], clone)
    return path


@pytest.fixture(scope="module")
def linked(installed, prepared, tmp_path_factory):
    """A linked worktree of prepared."""
    path = tmp_path_factory.mktemp("linked parent ü").resolve() / "linked wt ü"
    with on_machine(installed):
        add_worktree(prepared, path, "linked-fixture")
    yield path
    with on_machine(installed):
        success(["git", "worktree", "remove", "--force", path], prepared)


def test_linked_worktree_commits_with_its_clones_launcher(
    installed, prepared, tmp_path
):
    status = success(["git", "status", "--porcelain"], prepared).stdout
    worktree = add_worktree(prepared, tmp_path.resolve() / "linked wt ü", "linked")
    try:
        assert installed.cli(worktree, "status")["code"] == "healthy"
        minimal = dict(installed.environment, **MINIMAL_PATH, UV_PYTHON="/nonexistent")
        commit_hygiene(worktree, minimal)
        assert success(["git", "status", "--porcelain"], prepared).stdout == status
        part = worktree / "part.txt"
        part.write_text("staged\n")
        success(["git", "add", part.name], worktree)
        part.write_text("staged\nunstaged\n")
        success(["git", "commit", "-m", "Partial"], worktree)
        assert success(["git", "show", "HEAD:part.txt"], worktree).stdout == "staged\n"
        assert part.read_text() == "staged\nunstaged\n"
        assert installed.cli(worktree, "doctor")["code"] == "healthy"
        assert installed.cli(prepared, "doctor")["code"] == "healthy"
        moved = worktree.with_name("moved wt ü")
        success(["git", "worktree", "move", worktree, moved], prepared)
        worktree = moved
        (moved / "moved.txt").write_text("moved\n")
        success(["git", "add", "moved.txt"], moved)
        committed = success(["git", "commit", "-m", "Moved"], moved)
        assert "Passed" in committed.stdout + committed.stderr
        assert installed.cli(moved, "doctor")["code"] == "healthy"
    finally:
        success(["git", "worktree", "remove", "--force", worktree], prepared)
    success(["git", "commit", "--allow-empty", "-m", "Main"], prepared)


def bare_linked_worktree(origin, tmp_path):
    bare = tmp_path / "bare ü.git"
    success(["git", "clone", "-q", "--bare", origin, bare], tmp_path)
    worktree = tmp_path / "bare wt ü"
    success(["git", "worktree", "add", "-q", worktree, "main"], bare)
    return worktree, bare


def separate_git_dir(origin, tmp_path):
    common = tmp_path / "separate ü.git"
    worktree = tmp_path / "separate clone ü"
    success(
        ["git", "clone", "-q", "--separate-git-dir", common, origin, worktree],
        tmp_path,
    )
    return worktree, common


@pytest.mark.parametrize("layout", [bare_linked_worktree, separate_git_dir])
def test_other_layouts_commit_with_the_launcher_of_their_git_directory(
    installed, tmp_path, layout
):
    origin = published_origin(installed, tmp_path)
    worktree, common = layout(origin, tmp_path.resolve())
    success(["git", "config", "user.name", "gateseal test"], worktree)
    success(["git", "config", "user.email", "gateseal@example.invalid"], worktree)
    launcher = (common / "hooks" / "pre-commit").read_bytes()
    assert launcher == (git_path(origin, "hooks/pre-commit")).read_bytes()
    commit_hygiene(worktree)
    assert installed.cli(worktree, "status")["code"] == "healthy"
    if layout is bare_linked_worktree:
        refused = installed.cli(common, "status", code=2)
        assert refused["code"] == "not_a_repository"


MODES = {
    "all": ["-a"],
    "path": ["{modes}"],
    "include": ["-i", "{modes}"],
    "alternate": [],
}


def staged_names(root):
    listed = success(["git", "diff", "--cached", "--name-only"], root).stdout
    return set(listed.split())


def unstaged_names(root):
    return set(success(["git", "diff", "--name-only"], root).stdout.split())


@pytest.mark.parametrize(
    ("mode", "where"),
    [
        ("all", "prepared"),
        ("path", "prepared"),
        ("include", "prepared"),
        ("alternate", "prepared"),
        ("all", "linked"),
    ],
)
def test_commit_modes_check_the_index_they_commit(
    installed, request, tmp_path, mode, where
):
    root = request.getfixturevalue(where)
    modes, other, notes = (
        root / f"{mode}-{name}.txt" for name in ("modes", "other", "notes")
    )
    modes.write_text("committed\n")
    notes.write_text("notes\n")
    success(["git", "add", modes.name, notes.name], root)
    success(["git", "commit", "-m", "Track samples"], root)
    other.write_text("other\n")
    success(["git", "add", other.name], root)
    notes.write_text("notes\nunstaged\n")
    staged = staged_names(root)
    env = None
    if mode == "alternate":
        env = dict(os.environ, GIT_INDEX_FILE=str(tmp_path / "alternate index ü"))
        success(["git", "read-tree", "HEAD"], root, env)
    modes.write_text("changed  \n")
    if mode == "alternate":
        success(["git", "add", modes.name], root, env)
    command = ["git", "commit", "-m", "Mode"]
    command += [part.format(modes=modes.name) for part in MODES[mode]]
    try:
        failed = process(command, root, env)
        output = failed.stdout + failed.stderr
        assert failed.returncode != 0, output
        assert f"Fixing {modes.name}" in output
        assert "gateseal:" not in output
        assert modes.read_text() == "changed\n"
        assert staged_names(root) == staged
        if mode == "alternate":
            success(["git", "add", modes.name], root, env)
        success(command, root, env)
        assert success(["git", "show", f"HEAD:{modes.name}"], root).stdout == (
            "changed\n"
        )
        in_head = process(["git", "cat-file", "-e", f"HEAD:{other.name}"], root)
        if mode in ("path", "alternate"):
            assert other.name in staged_names(root)
            assert in_head.returncode != 0
            assert notes.name in unstaged_names(root)
        else:
            assert in_head.returncode == 0
        assert notes.read_text() == "notes\nunstaged\n"
    finally:
        success(["git", "reset", "-q", "--hard"], root)
        other.unlink(missing_ok=True)


COMMANDS = ("status", "check", "run", "gate", "doctor", "configure")


def test_public_commands_refuse_an_alternate_index(
    installed, prepared, linked, tmp_path
):
    alternate = tmp_path / "alt index ü"
    success(
        ["git", "read-tree", "HEAD"],
        prepared,
        dict(os.environ, GIT_INDEX_FILE=str(alternate)),
    )
    tracked = {name: (prepared / name).read_bytes() for name in TRACKED}
    env = dict(installed.environment, GIT_INDEX_FILE=str(alternate))
    for command in COMMANDS:
        refused = installed.cli(prepared, command, code=2, env=env)
        assert refused["code"] == "alternate_index", command
        assert refused["next_steps"] == ["Unset GIT_INDEX_FILE, then retry."]
    assert tracked == {name: (prepared / name).read_bytes() for name in TRACKED}
    default = dict(installed.environment, GIT_INDEX_FILE=".git/index")
    assert installed.cli(prepared, "status", env=default)["code"] == "healthy"
    own = dict(installed.environment, GIT_INDEX_FILE=str(git_path(linked, "index")))
    assert installed.cli(linked, "status", env=own)["code"] == "healthy"


def test_linked_worktree_whose_path_has_shell_characters_commits(
    installed, configured, tmp_path
):
    success(["git", "add", *TRACKED], configured)
    success(["git", "commit", "-m", "Adopt hygiene"], configured)
    worktree = add_worktree(configured, tmp_path.resolve() / "wt a=b $c 'd'", "eq")
    commit_hygiene(worktree)
    assert installed.cli(worktree, "doctor")["code"] == "healthy"
