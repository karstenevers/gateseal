"""A Repository Maintainer moves a slow hook from each commit to each push.

Each test configures a plain directory in process, as test_repository_hooks
does. The push hook's table comes from the Manifest reference.
"""

import json
from pathlib import Path

import pytest
from doc_examples import example
from gateseal import lock
from gateseal.compiler_release import (
    LFS_HOOK,
    LFS_HOOK_NAME,
    NODE_PACKAGES_HOOK,
    NODE_PACKAGES_HOOK_NAME,
    REPOSITORY,
)
from test_lock_configure import CONFIG, preparation
from test_repository_hooks import LICENSES, MIGRATIONS, configure, refused, repository

SLOW = "\n" + example("policy-contract.md", "manifest tables: push hook")
PUSH = 'stages = ["push", "ci"]\n'
REFUSED = (
    'hooks.check-licenses.stages must be ["commit", "run", "ci"], ["push", "ci"] '
    'or ["ci"].'
)


def configured(tmp_path, *tables):
    """Configure a worktree whose Manifest holds tables, and return its config."""
    root = repository(tmp_path, *tables)
    (root / "scripts/slow-tests.sh").write_text("#!/bin/sh\n")
    configure(root)
    return root, json.loads((root / CONFIG).read_bytes())


def test_a_push_hook_asks_for_the_push_launcher_and_the_git_lfs_check(tmp_path):
    root, config = configured(tmp_path, MIGRATIONS, SLOW)
    assert config["default_install_hook_types"] == ["pre-commit", "pre-push"]
    own, *_, local = config["repos"]
    assert own["repo"] == REPOSITORY
    assert own["hooks"] == [{"id": "gateseal-check"}, {"id": LFS_HOOK}]
    assert [(hook["id"], hook["stages"]) for hook in local["hooks"]] == [
        ("check-migrations", ["pre-commit", "manual"]),
        ("slow-tests", ["pre-push", "manual"]),
    ]
    assert lock.load(root, preparation(root)).selection.pushes


@pytest.mark.parametrize(
    ("listed", "stages"),
    [
        ('["commit", "run", "ci"]', ["pre-commit", "manual"]),
        ('["ci", "run", "commit"]', ["pre-commit", "manual"]),
        ('["ci"]', ["manual"]),
    ],
    ids=["commit", "any order", "gate alone"],
)
def test_a_selection_without_a_push_hook_asks_for_no_push_launcher(
    tmp_path, listed, stages
):
    root, config = configured(tmp_path, LICENSES + f"stages = {listed}\n")
    assert "default_install_hook_types" not in config
    own, *_, local = config["repos"]
    assert own["hooks"] == [{"id": "gateseal-check"}]
    assert [hook["stages"] for hook in local["hooks"]] == [stages]
    assert not lock.load(root, preparation(root)).selection.pushes


def test_a_hook_that_lists_the_default_stages_compiles_to_the_same_bytes(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "listed").mkdir()
    plain, _ = configured(tmp_path / "plain", LICENSES)
    listed, _ = configured(
        tmp_path / "listed", LICENSES + 'stages = ["commit", "run", "ci"]\n'
    )
    assert (plain / CONFIG).read_bytes() == (listed / CONFIG).read_bytes()


@pytest.mark.parametrize(
    "listed",
    [
        '["push"]',
        '["commit", "run", "push", "ci"]',
        '["commit", "ci"]',
        '["push", "push", "ci"]',
        '["deploy"]',
        "[]",
    ],
    ids=["push alone", "commit and push", "no run", "twice", "unknown", "none"],
)
def test_a_hook_lists_one_of_the_three_stage_sets(tmp_path, listed):
    root = repository(tmp_path, LICENSES + f"stages = {listed}\n")
    assert str(refused(root, "invalid_field")) == REFUSED


@pytest.mark.parametrize("value", ['"push"', '[["push", "ci"]]', "[1]"])
def test_stages_is_a_list_of_names(tmp_path, value):
    root = repository(tmp_path, LICENSES + f"stages = {value}\n")
    assert "hooks.check-licenses.stages" in str(refused(root, "invalid_field"))


@pytest.mark.parametrize(
    ("hook", "name"),
    [(LFS_HOOK, LFS_HOOK_NAME), (NODE_PACKAGES_HOOK, NODE_PACKAGES_HOOK_NAME)],
    ids=["git lfs check", "node packages"],
)
@pytest.mark.parametrize("taken", ["ID", "name"])
def test_a_repository_hook_cannot_take_the_place_of_a_hook_of_gateseal(
    tmp_path, hook, name, taken
):
    table, message = {
        "ID": (
            SLOW.replace("slow-tests]", f"{hook}]"),
            f"hooks.{hook} has the ID {hook}, which gateseal's own hook has.",
        ),
        "name": (
            SLOW.replace("Run the slow tests", name),
            f"hooks.slow-tests has the name {name}, which gateseal's own hook has.",
        ),
    }[taken]
    root = repository(tmp_path, table)
    (root / "scripts/slow-tests.sh").write_text("#!/bin/sh\n")
    assert str(refused(root, "duplicate_identifier")) == message


def test_the_git_lfs_check_name_is_the_one_pre_commit_prints():
    hooks = (Path(__file__).parents[1] / ".pre-commit-hooks.yaml").read_text()
    assert f"- id: {LFS_HOOK}\n  name: {LFS_HOOK_NAME}\n" in hooks
