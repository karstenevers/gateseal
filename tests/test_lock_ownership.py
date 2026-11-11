"""gateseal replaces or deletes only the files it wrote, as its Lock records them."""

import json
import stat

import pytest
from gateseal import lock
from test_lock_configure import (
    CHECKSUM,
    CI_TABLE,
    CONFIG,
    LOCK,
    MANIFEST,
    RUFF,
    SOURCE,
    WORKFLOW,
    configure,
    configured,
    edit,
    edit_lock,
    outcome,
    refused,
    sha,
    tracked,
    worktree,
)

EXCLUDE = '\n[choices.hygiene]\nexclude = ["vendor"]\n'


def conflicted(error, name):
    """Check that error keeps name, a file gateseal did not write, for the team."""
    assert outcome(error) == ("ownership_conflict", "")
    assert name in str(error)


def test_a_file_gateseal_did_not_write_is_kept(tmp_path):
    root = worktree(tmp_path)
    (root / CONFIG).write_bytes(b"repos: []\n")
    error = refused(configure, root, ["hygiene"], source=SOURCE, checksum=CHECKSUM)
    conflicted(error, CONFIG)
    assert tracked(root) == {CONFIG: b"repos: []\n"}


def test_a_changed_selection_replaces_the_outputs_gateseal_wrote(tmp_path):
    root = configured(tmp_path)
    edit(root, MANIFEST, lambda text: text + EXCLUDE)
    assert configure(root)[0] == (LOCK, CONFIG)
    assert "^(?:vendor(?:/|$))" in (root / CONFIG).read_text()
    recorded = json.loads((root / LOCK).read_bytes())["outputs"][CONFIG]["sha256"]
    assert recorded == sha((root / CONFIG).read_bytes())


def test_an_edited_output_stops_a_changed_selection(tmp_path):
    root = configured(tmp_path)
    edit(root, CONFIG, lambda text: text + "# kept by hand\n")
    edit(root, MANIFEST, lambda text: text + EXCLUDE)
    before = tracked(root)
    conflicted(refused(configure, root), CONFIG)
    assert tracked(root) == before


def drop_python(text):
    return text.replace('["hygiene", "python"]', '["hygiene"]')


def drop_ci(text):
    return text.replace(CI_TABLE, "")


@pytest.mark.parametrize(
    "drop,changed,gone",
    [
        (drop_python, (LOCK, CONFIG, RUFF), RUFF),
        (drop_ci, (LOCK, WORKFLOW), WORKFLOW),
    ],
    ids=["python", "ci"],
)
def test_an_output_the_selection_drops_is_deleted(tmp_path, drop, changed, gone):
    root = configured(tmp_path, "hygiene", "python", ci="github")
    edit(root, MANIFEST, drop)
    assert configure(root)[0] == changed
    assert not (root / gone).exists()
    assert gone not in json.loads((root / LOCK).read_bytes())["outputs"]
    assert configure(root)[0] == ()


def test_an_edited_output_the_selection_drops_is_kept(tmp_path):
    root = configured(tmp_path, ci="github")
    edit(root, WORKFLOW, lambda text: text + "# kept by hand\n")
    edit(root, MANIFEST, drop_ci)
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == "ownership_conflict"
    assert WORKFLOW in str(error)
    assert WORKFLOW in error.next_step
    assert "gateseal configure" in error.next_step
    assert tracked(root) == before


def test_a_dropped_output_already_deleted_needs_no_removal(tmp_path):
    root = configured(tmp_path, ci="github")
    (root / WORKFLOW).unlink()
    edit(root, MANIFEST, drop_ci)
    assert configure(root)[0] == (LOCK,)


@pytest.mark.parametrize("name", ["README.md", ".gateseal-notes", "docs/.gateseal/x"])
def test_a_lock_cannot_name_another_file_for_deletion(tmp_path, name):
    root = configured(tmp_path)
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(b"the team's own file\n")
    entry = {"sha256": sha(b"the team's own file\n")}
    edit_lock(root, lambda document: document["outputs"].update({name: entry}))
    assert configure(root)[0] == (LOCK,)
    assert (root / name).read_bytes() == b"the team's own file\n"


def test_an_upgrade_replaces_the_manifest_its_lock_records(tmp_path):
    root = configured(tmp_path)
    recorded = json.loads((root / LOCK).read_bytes())
    manifest = (root / MANIFEST).read_bytes() + EXCLUDE.encode()
    planned = {MANIFEST: manifest, LOCK: b"{}\n", CONFIG: (root / CONFIG).read_bytes()}
    changes = lock.owned_changes(root, planned, recorded)
    assert changes == lock.Changes({MANIFEST: manifest, LOCK: b"{}\n"}, ())
    assert changes.names == (MANIFEST, LOCK)


def test_an_upgrade_keeps_a_manifest_edited_since_its_lock(tmp_path):
    root = configured(tmp_path)
    recorded = json.loads((root / LOCK).read_bytes())
    edit(root, MANIFEST, lambda text: text + EXCLUDE)
    planned = {MANIFEST: b"schema = 1\n", LOCK: b"{}\n"}
    conflicted(refused(lock.owned_changes, root, planned, recorded), MANIFEST)


def test_write_tracked_writes_outputs_then_the_manifest_and_lock(tmp_path):
    root = configured(tmp_path)
    writes = {MANIFEST: b"manifest\n", LOCK: b"lock\n", ".gateseal/new.toml": b"new\n"}
    lock.write_tracked(root, lock.Changes(writes, (CONFIG,)))
    assert tracked(root) == writes
    for name in writes:
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o644, name


def test_an_interrupted_write_leaves_the_old_lock(tmp_path):
    root = configured(tmp_path)
    before = tracked(root)
    (root / ".gateseal" / "blocked.toml").mkdir(parents=True)
    writes = {MANIFEST: b"manifest\n", LOCK: b"lock\n", ".gateseal/blocked.toml": b""}
    with pytest.raises(OSError):
        lock.write_tracked(root, lock.Changes(writes, (CONFIG,)))
    assert tracked(root) == before
