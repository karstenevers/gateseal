"""load and validate_snapshot check a Manifest, Lock and outputs together.

describe and review_steps then name what the checked worktree selects.
"""

import json
from datetime import date

import pytest
from gateseal import __version__, lock
from gateseal.compiler_release import CompilerArtifact
from gateseal.manifest import BundledSource
from gateseal.validation import canonical
from test_lock_configure import (
    CHECKSUM,
    CONFIG,
    LOCK,
    MANIFEST,
    OTHER_RELEASE,
    POLICY,
    SOURCE,
    WHEEL,
    check_policy_note,
    configure,
    configured,
    edit,
    other_compiler,
    preparation,
    refused,
)

ARTIFACT = CompilerArtifact("https-wheel", SOURCE, WHEEL, CHECKSUM, __version__)
EXCEPTION = (
    '\n[exceptions.pre-commit-hooks]\nversion = "5.0.0"\n'
    'reason = "the next release rejects our generated YAML"\nreview_by = 2098-12-31\n'
)
EXCEPTION_VALUES = (
    "pre-commit-hooks",
    "5.0.0",
    "6.0.0",
    "2098-12-31",
    "the next release rejects our generated YAML",
)


def files(root, *names):
    return {name: (root / name).read_bytes() for name in names}


def test_load_returns_the_checked_worktree(tmp_path):
    root = configured(tmp_path)
    locked = lock.load(root, preparation(root))
    assert (locked.root, locked.policy, locked.artifact, locked.source) == (
        root,
        POLICY,
        ARTIFACT,
        BundledSource(POLICY.release),
    )
    assert locked.snapshot == lock.Snapshot(
        (root / MANIFEST).read_bytes(),
        (root / LOCK).read_bytes(),
        files(root, CONFIG),
    )
    assert [tool.definition.name for tool in locked.selection.tools] == [
        "pre-commit-hooks"
    ]
    assert lock.validate_snapshot(root, locked.snapshot, preparation(root)) == locked


def test_load_reads_tracked_files_as_git_stores_them(tmp_path):
    root = configured(tmp_path)
    expected = lock.load(root, preparation(root)).snapshot
    edit(root, MANIFEST, lambda text: text.replace("\n", "\r\n"))
    assert lock.load(root, preparation(root)).snapshot == expected


def test_an_edited_output_is_named_unless_outputs_are_left_unread(tmp_path):
    root = configured(tmp_path)
    compiled = files(root, CONFIG)
    edit(root, CONFIG, lambda text: text + "# edited\n")
    error = refused(lock.load, root, preparation(root))
    assert error.code == "ownership_conflict"
    assert CONFIG in str(error)
    unread = lock.load(root, preparation(root), outputs=False)
    assert unread.snapshot.outputs == compiled


@pytest.mark.parametrize("missing", [MANIFEST, LOCK, CONFIG])
def test_load_needs_every_configured_file(tmp_path, missing):
    root = configured(tmp_path)
    (root / missing).unlink()
    error = refused(lock.load, root, preparation(root))
    assert error.code == "missing_configuration"
    assert "gateseal configure" in str(error)


def test_an_executable_output_is_refused(tmp_path):
    root = configured(tmp_path)
    (root / CONFIG).chmod(0o755)
    error = refused(lock.load, root, preparation(root))
    assert error.code == "file_mode_mismatch"
    assert all(value in str(error) for value in (CONFIG, "0755"))


def lock_edit(change):
    def snapshot(locked):
        document = json.loads(locked.snapshot.lock)
        change(document)
        return lock.Snapshot(
            locked.snapshot.manifest, canonical(document), locked.snapshot.outputs
        )

    return snapshot


def outputs_edit(change):
    def snapshot(locked):
        outputs = dict(locked.snapshot.outputs)
        change(outputs)
        return lock.Snapshot(locked.snapshot.manifest, locked.snapshot.lock, outputs)

    return snapshot


def manifest_edit(locked):
    manifest = locked.snapshot.manifest + b'\n[choices.hygiene]\nexclude = ["v"]\n'
    return lock.Snapshot(manifest, locked.snapshot.lock, locked.snapshot.outputs)


FIELDS = (
    "choices",
    "compiler",
    "components",
    "exceptions",
    "inputs_fingerprint",
    "manifest_sha256",
    "native_artifacts",
    "outputs",
    "policy",
    "runtime",
    "schema",
)
CONFIGURE = "gateseal configure"


def surprise_from_another_compiler(document):
    other_compiler(document)
    document["surprise"] = 1


@pytest.mark.parametrize(
    "change,code,step,named",
    [
        (
            lock_edit(lambda d: d["choices"]["hygiene"].update(exclude=["v"])),
            "lock_mismatch",
            CONFIGURE,
            (),
        ),
        (manifest_edit, "lock_mismatch", CONFIGURE, ()),
        (outputs_edit(dict.clear), "lock_mismatch", "", ()),
        (
            outputs_edit(lambda o: o.update(extra=b"")),
            "lock_mismatch",
            "",
            (),
        ),
        (
            outputs_edit(lambda o: o.update({CONFIG: o[CONFIG] + b"#\n"})),
            "ownership_conflict",
            "",
            (CONFIG,),
        ),
        (lock_edit(lambda d: d.update(schema=2)), "unsupported_schema", "", ("1",)),
        (lock_edit(lambda d: d.update(schema=True)), "invalid_field", "", ("schema",)),
        (lock_edit(lambda d: d.update(surprise=1)), "invalid_field", "", FIELDS),
        (
            lock_edit(surprise_from_another_compiler),
            "selected_compiler_required",
            "",
            (OTHER_RELEASE,),
        ),
    ],
    ids=[
        "lock choices",
        "manifest choices",
        "no outputs",
        "extra output",
        "edited output",
        "schema 2",
        "boolean schema",
        "unknown field",
        "another compiler first",
    ],
)
def test_validate_snapshot_refuses_disagreement(tmp_path, change, code, step, named):
    root = configured(tmp_path)
    snapshot = change(lock.load(root, preparation(root)))
    error = refused(lock.validate_snapshot, root, snapshot, preparation(root))
    assert (error.code, error.next_step) == (code, step)
    assert all(value in str(error) for value in named)


def with_exception(tmp_path):
    root = configured(tmp_path)
    edit(root, MANIFEST, lambda text: text + EXCEPTION)
    configure(root)
    return lock.load(root, preparation(root))


def test_describe_names_the_policy_runtime_tools_and_choices(tmp_path):
    root = configured(tmp_path)
    notes = lock.describe(lock.load(root, preparation(root)))
    check_policy_note(notes[0])
    assert [note.category for note in notes] == [
        "policy",
        "runtime",
        "tool",
        "policy_choice",
    ]
    assert lock.review_steps(notes) == ()


def test_an_exception_is_overdue_only_after_its_review_date(tmp_path):
    locked = with_exception(tmp_path)
    due = lock.describe(locked, date(2098, 12, 31))[-1]
    assert (due.severity, due.category) == ("info", "version_exception")
    assert all(value in due.message for value in EXCEPTION_VALUES)
    overdue = lock.describe(locked, date(2099, 1, 1))
    assert all(value in overdue[-1].message for value in EXCEPTION_VALUES)
    assert (overdue[-1].severity, overdue[-1].category) == (
        "warning",
        "overdue_exception",
    )
    assert lock.describe(locked)[-1] == due


def test_review_steps_follow_a_warning_among_other_notes(tmp_path):
    locked = with_exception(tmp_path)
    assert lock.review_steps(lock.describe(locked, date(2098, 12, 31))) == ()
    notes = lock.describe(locked, date(2099, 1, 1))
    assert [note.severity for note in notes].count("warning") == 1
    (step,) = lock.review_steps(notes)
    assert all(value in step for value in (MANIFEST, "review_by", CONFIGURE))
    assert lock.review_steps(()) == ()
