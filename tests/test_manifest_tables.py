"""A Manifest may leave out [choices] and [exceptions]: a missing table is empty.

configure and upgrade run in process with the bundled policy and conftest's
PIN for gateseal's release tag, so the network stays off.
"""

import json

import pytest
from gateseal import upgrade
from test_lock_configure import LOCK, MANIFEST, configure, tracked, worktree
from test_upgrade_worktree import REASON, REVIEW_BY, request
from test_upgrade_worktree import configure as configure_worktree

TABLES = "\n[choices]\n\n[exceptions]\n"
HOOKS_5 = (
    '\n[exceptions.pre-commit-hooks]\nversion = "5.0.0"\n'
    f'reason = "{REASON}"\nreview_by = {REVIEW_BY.isoformat()}\n'
)


def generated(root):
    """Return the generated files, and the Lock without the Manifest's bytes.

    The Lock records the Manifest's digest, so that check sees any edit, and
    its inputs fingerprint covers that digest.
    """
    files = tracked(root)
    del files[MANIFEST]
    document = json.loads(files.pop(LOCK))
    del document["manifest_sha256"], document["inputs_fingerprint"]
    return files, document


@pytest.mark.parametrize(
    "left_out",
    [["[choices]"], ["[exceptions]"], ["[choices]", "[exceptions]"]],
    ids=["choices", "exceptions", "both"],
)
def test_a_missing_table_gives_the_files_of_an_empty_one(tmp_path, left_out):
    root = worktree(tmp_path)
    configure(root, ["hygiene", "secrets"])
    manifest = root / MANIFEST
    with_tables = manifest.read_text() + TABLES
    manifest.write_text(with_tables)
    configure(root)
    expected = generated(root)
    without = with_tables
    for table in left_out:
        without = without.replace(f"\n{table}\n", "")
    manifest.write_text(without)
    assert configure(root)[0] == (LOCK,)
    assert generated(root) == expected


def test_configure_and_upgrade_keep_the_empty_tables(consumer):
    worktree = configure_worktree(consumer, "hygiene")
    manifest = consumer / MANIFEST
    with_tables = manifest.read_text() + TABLES
    manifest.write_text(with_tables)
    assert configure(consumer)[0] == (LOCK,)
    assert configure(consumer)[0] == ()
    exception = request(
        components={"pre-commit-hooks": "5.0.0"}, flags=(REASON, REVIEW_BY)
    )
    assert upgrade.run(worktree, exception).code == "upgraded"
    assert manifest.read_text() == with_tables + HOOKS_5
    upgrade.run(worktree, request(components={"pre-commit-hooks": None}))
    assert manifest.read_text() == with_tables


def test_upgrade_adds_and_removes_an_exception_without_the_table(consumer):
    worktree = configure_worktree(consumer, "hygiene")
    manifest = consumer / MANIFEST
    new = manifest.read_text()
    exception = request(
        components={"pre-commit-hooks": "5.0.0"}, flags=(REASON, REVIEW_BY)
    )
    assert upgrade.run(worktree, exception).code == "upgraded"
    assert manifest.read_text() == new + HOOKS_5
    assert upgrade.run(worktree, request()).code == "unchanged"
    upgrade.run(worktree, request(components={"pre-commit-hooks": None}))
    assert manifest.read_text() == new
