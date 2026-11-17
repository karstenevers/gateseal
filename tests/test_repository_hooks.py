"""A Repository Maintainer adds hooks that run scripts of the repository.

Each test configures a plain directory in process with the bundled policy, as
test_lock_configure does, then reads what gateseal wrote or refused. The
[hooks] table comes from the Manifest reference. The bundled selections'
golden digests in test_tool_definitions show that a Manifest without [hooks]
still compiles to the same bytes.
"""

import json
import os
from pathlib import Path

import pytest
from conftest import with_git_config
from doc_examples import example
from gateseal import lock
from gateseal.compiler_release import CHECK_HOOK, CHECK_HOOK_NAME, REPOSITORY
from gateseal.diagnostics import GatesealError
from team_policy import URL, git, publish
from test_lock_configure import CONFIG, MANIFEST, POLICY, preparation, worktree
from test_upgrade_worktree import adopted, request
from test_upgrade_worktree import refused as upgrade_refused

MIGRATIONS = "\n" + example("policy-contract.md", "manifest tables: hooks")
LICENSES = '\n[hooks.check-licenses]\nname = "Check licenses"\nrun = "bin/licenses"\n'


def manifest(*tables):
    return (
        "schema = 1\n"
        'capabilities = ["hygiene"]\n\n'
        f'[policy]\nsource = "bundled"\nrelease = "{POLICY.release}"\n\n'
        "[choices]\n\n[exceptions]\n" + "".join(tables)
    )


def repository(tmp_path, *tables):
    """Return a worktree whose Manifest holds tables, with both scripts."""
    root = worktree(tmp_path)
    for name in ("scripts/check-migrations.sh", "bin/licenses"):
        (root / name).parent.mkdir(exist_ok=True)
        (root / name).write_text("#!/bin/sh\n")
    (root / MANIFEST).write_text(manifest(*tables))
    return root


def configure(root):
    return lock.configure(
        root, "git", preparation(root), None, lock.PolicyRequest(), None
    )


def refused(root, code):
    before = sorted(path.name for path in root.iterdir())
    with pytest.raises(GatesealError) as raised:
        configure(root)
    assert raised.value.code == code
    assert sorted(path.name for path in root.iterdir()) == before
    return raised.value


def test_configure_writes_repository_hooks_after_every_policy_hook(tmp_path):
    root = repository(tmp_path, MIGRATIONS, LICENSES)
    _, notes = configure(root)
    *policy, last = json.loads((root / CONFIG).read_bytes())["repos"]
    assert [entry["repo"] for entry in policy] == [
        REPOSITORY,
        "https://github.com/pre-commit/pre-commit-hooks",
    ]
    stages = ["pre-commit", "manual"]
    assert last == {
        "repo": "local",
        "hooks": [
            {
                "id": "check-migrations",
                "name": "Check migration numbers",
                "language": "unsupported_script",
                "entry": "scripts/check-migrations.sh --strict",
                "files": "^migrations/",
                "stages": stages,
            },
            {
                "id": "check-licenses",
                "name": "Check licenses",
                "language": "unsupported_script",
                "entry": "bin/licenses",
                "stages": stages,
            },
        ],
    }
    assert [note.message for note in notes if note.category == "repository_hook"] == [
        "Repository hook check-migrations: scripts/check-migrations.sh --strict, on "
        "files matching ^migrations/.",
        "Repository hook check-licenses: bin/licenses, on every file.",
    ]
    hooks = lock.load(root, preparation(root)).selection.hooks
    assert [(hook.id, str(hook.origin), hook.tool) for hook in hooks[-2:]] == [
        ("check-migrations", "repository", "scripts/check-migrations.sh"),
        ("check-licenses", "repository", "bin/licenses"),
    ]


def test_a_changed_hooks_table_needs_configure(tmp_path):
    root = repository(tmp_path, MIGRATIONS)
    configure(root)
    path = root / MANIFEST
    path.write_text(path.read_text().replace("--strict", "--quick"))
    with pytest.raises(GatesealError) as raised:
        lock.load(root, preparation(root))
    assert raised.value.code == "lock_mismatch"


@pytest.mark.parametrize(
    ("table", "message"),
    [
        (
            MIGRATIONS.replace("scripts/check-migrations.sh", "/usr/local/bin/check"),
            "hooks.check-migrations.run is '/usr/local/bin/check', but a repository "
            "hook runs a file of the repository, named by its path from the "
            "repository root, such as scripts/check.sh.",
        ),
        (
            MIGRATIONS.replace("scripts/check-migrations.sh", "../outside.sh"),
            "hooks.check-migrations.run is '../outside.sh', but a repository hook "
            "runs a file of the repository, named by its path from the repository "
            "root, such as scripts/check.sh.",
        ),
        (
            MIGRATIONS + 'stages = ["pre-push"]\n',
            "hooks.check-migrations has stages, which a repository hook does not "
            "take. It takes name, run, args and files.",
        ),
        (
            MIGRATIONS.replace('"^migrations/"', '"(migrations"'),
            "hooks.check-migrations.files is not a regular expression: missing ), "
            "unterminated subpattern at position 0.",
        ),
        (
            MIGRATIONS.replace("hooks.check-migrations", "hooks.Check"),
            "hooks.Check needs an ID of lowercase letters, digits and -.",
        ),
        (
            MIGRATIONS.replace('name = "Check migration numbers"\n', ""),
            "hooks.check-migrations needs name.",
        ),
        (
            MIGRATIONS.replace('run = "scripts/check-migrations.sh"\n', ""),
            "hooks.check-migrations needs run.",
        ),
        (
            '\n[hooks.check-migrations]\nargs = ["--strict"]\n',
            "hooks.check-migrations needs name and run.",
        ),
    ],
    ids=[
        "absolute",
        "outside",
        "stages",
        "pattern",
        "id",
        "no name",
        "no run",
        "neither",
    ],
)
def test_a_hook_table_names_its_hook_and_field_when_refused(tmp_path, table, message):
    error = refused(repository(tmp_path, table), "invalid_field")
    assert str(error) == message


def test_run_names_a_file_in_the_repository(tmp_path):
    root = repository(tmp_path, LICENSES.replace("bin/licenses", "bin/missing"))
    error = refused(root, "invalid_field")
    assert str(error) == (
        "hooks.check-licenses.run names bin/missing, which is not a file in the "
        "repository."
    )
    assert error.next_step == (
        "Add bin/missing, or name another file in hooks.check-licenses.run. Then "
        "run gateseal configure."
    )
    (root / "bin/missing").mkdir()
    refused(root, "invalid_field")
    (root / "bin/missing").rmdir()
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/sh\n")
    os.symlink(outside, root / "bin/missing")
    refused(root, "invalid_field")
    (root / "bin/missing").unlink()
    os.symlink("licenses", root / "bin/missing")
    configure(root)


@pytest.mark.parametrize(
    ("table", "message"),
    [
        (
            MIGRATIONS.replace("check-migrations]", "check-yaml]"),
            "hooks.check-yaml has the ID check-yaml, which a hook of the selected "
            "policy has.",
        ),
        (
            MIGRATIONS.replace("check-migrations]", "ruff-check]"),
            "hooks.ruff-check has the ID ruff-check, which a hook of the selected "
            "policy has.",
        ),
        (
            MIGRATIONS.replace("check-migrations]", "gateseal-check]"),
            "hooks.gateseal-check has the ID gateseal-check, which gateseal's own "
            "check hook has.",
        ),
        (
            MIGRATIONS.replace("Check migration numbers", CHECK_HOOK_NAME),
            "hooks.check-migrations has the name Check files and tools against "
            "gateseal.lock, which gateseal's own check hook has.",
        ),
        (
            MIGRATIONS.replace("Check migration numbers", "check yaml"),
            "hooks.check-migrations has the name check yaml, which a hook of the "
            "selected policy has.",
        ),
        (
            MIGRATIONS + LICENSES.replace("Check licenses", "Check migration numbers"),
            "hooks.check-licenses has the name Check migration numbers, which "
            "hooks.check-migrations has.",
        ),
    ],
    ids=[
        "selected hook",
        "unselected hook",
        "check hook",
        "check hook name",
        "hook name",
        "own name",
    ],
)
def test_a_repository_hook_only_adds_a_hook(tmp_path, table, message):
    error = refused(repository(tmp_path, table), "duplicate_identifier")
    assert str(error) == message
    assert "another" in error.next_step


def test_the_check_hook_name_is_the_one_pre_commit_prints():
    hooks = (Path(__file__).parents[1] / ".pre-commit-hooks.yaml").read_text()
    assert f"- id: {CHECK_HOOK}\n  name: {CHECK_HOOK_NAME}\n" in hooks


def without_check_yaml(data):
    toml = data / "policy.toml"
    toml.write_text(toml.read_text().replace(', "check-yaml"]', "]"))


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """Publish v1 without the check-yaml hook, and v2 as bundled."""
    origin = tmp_path_factory.mktemp("hooks team ü") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", without_check_yaml)
    publish(origin, "v2")
    return origin


def test_an_upgrade_stops_at_a_policy_hook_with_a_repository_hooks_id(
    consumer, team, monkeypatch
):
    settings = {f"url.{team.as_uri()}.insteadOf": URL}
    for key, value in with_git_config(dict(os.environ), settings).items():
        if key.startswith("GIT_CONFIG_"):
            monkeypatch.setenv(key, value)
    worktree = adopted(consumer, "v1")
    (consumer / "scripts").mkdir()
    (consumer / "scripts/check-migrations.sh").write_text("#!/bin/sh\n")
    path = consumer / MANIFEST
    path.write_text(
        path.read_text() + MIGRATIONS.replace("check-migrations]", "check-yaml]")
    )
    adopted(consumer, "v1")
    error = upgrade_refused(worktree, request(lock.PolicyRequest(tag="v2")))
    assert (error.code, str(error)) == (
        "duplicate_identifier",
        "hooks.check-yaml has the ID check-yaml, which a hook of the selected "
        "policy has.",
    )
