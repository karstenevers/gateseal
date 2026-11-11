import subprocess

import pytest
from gateseal import bootstrap
from installation import success
from test_bootstrap_record import LOCK, prepare

MANIFEST = b'[policy]\nsource = "bundled"\n'
MISE = ".gateseal/mise.toml"
CONFIG = ".pre-commit-config.yaml"
OUTPUTS = {MISE: b'[tools]\nruff = "0.17.1"\n', CONFIG: b"repos: []\n"}


def staged_repository(tmp_path):
    """Stage the Manifest, the Lock and the outputs that root's record names."""
    root = tmp_path / "consumer ü"
    success(["git", "init", "-q", root], tmp_path)
    root = root.resolve()
    files = {"gateseal.toml": MANIFEST, "gateseal.lock": LOCK, **OUTPUTS}
    (root / ".gateseal").mkdir()
    for name, data in files.items():
        (root / name).write_bytes(data)
    success(["git", "add", *files], root)
    return prepare(tmp_path / "preparation", root, outputs=tuple(OUTPUTS))


@pytest.fixture
def record(tmp_path):
    return staged_repository(tmp_path)


def blobs(root, *names):
    arguments = ["git", "rev-parse", *(":" + name for name in names)]
    return success(arguments, root).stdout.split()


def refusal(record):
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.check_staged(record)
    return str(raised.value), raised.value.next_step


def stage(root, name, data):
    (root / name).write_bytes(data)
    success(["git", "add", name], root)


def test_the_staged_policy_passes_with_its_blob_ids(record):
    expected = blobs(record.root, "gateseal.toml", "gateseal.lock", MISE, CONFIG)
    assert bootstrap.check_staged(record) == expected


def test_an_output_checked_out_with_crlf_line_endings_is_not_an_edit(record):
    success(["git", "config", "core.autocrlf", "true"], record.root)
    stage(record.root, CONFIG, OUTPUTS[CONFIG].replace(b"\n", b"\r\n"))
    assert (record.root / CONFIG).read_bytes() == b"repos: []\r\n"
    bootstrap.check_staged(record)


@pytest.mark.parametrize("name", ["gateseal.toml", "gateseal.lock", MISE])
def test_a_policy_file_missing_from_the_index_is_named(record, name):
    success(["git", "rm", "-q", "--cached", name], record.root)
    message, step = refusal(record)
    assert name in message
    assert all(f in step for f in ("gateseal.toml", "gateseal.lock", MISE, CONFIG))
    assert "--no-verify" in step


def index_entries(root, name, *entries):
    success(["git", "update-index", "--force-remove", name], root)
    lines = "".join(f"{mode} {oid} {stage}\t{name}\n" for mode, oid, stage in entries)
    subprocess.run(
        ["git", "update-index", "--index-info"],
        cwd=root,
        input=lines,
        text=True,
        check=True,
    )


@pytest.mark.parametrize("stages", [("1", "2", "3"), ("2",)], ids=["all", "ours"])
def test_a_conflicted_lock_must_be_resolved(record, stages):
    (oid,) = blobs(record.root, "gateseal.lock")
    entries = [("100644", oid, stage) for stage in stages]
    index_entries(record.root, "gateseal.lock", *entries)
    assert all("conflict" in t and "gateseal.lock" in t for t in refusal(record))


def test_an_executable_manifest_is_an_unsupported_type(record):
    success(["git", "update-index", "--chmod=+x", "gateseal.toml"], record.root)
    message, step = refusal(record)
    assert "gateseal.toml" in message
    assert "100755" in message
    assert "gateseal.toml" in step


INVALID_LOCKS = {
    "not json": (b"{", None),
    "array": (b"[1]", "schema"),
    "no schema": (b"{}", "schema"),
    "boolean schema": (b'{"schema":true}', "schema"),
    "schema 2": (b'{"schema":2}', "schema"),
}


@pytest.mark.parametrize(
    ("data", "problem"), INVALID_LOCKS.values(), ids=INVALID_LOCKS.keys()
)
def test_a_staged_lock_that_is_not_a_lock_points_to_check(record, data, problem):
    stage(record.root, "gateseal.lock", data)
    message, step = refusal(record)
    assert "gateseal.lock" in message
    assert problem is None or problem in message
    assert step == bootstrap.CHECK


OTHER_LOCK = b'{"schema":1,"compiler":"0.2.0"}\n'


def test_another_staged_lock_needs_activation_when_the_worktree_holds_it(record):
    stage(record.root, "gateseal.lock", OTHER_LOCK)
    message, step = refusal(record)
    assert "gateseal.lock" in message
    assert step == bootstrap.ACTIVATE


@pytest.mark.parametrize(
    "unstaged", [("gateseal.lock",), ("gateseal.toml", "gateseal.lock")]
)
def test_another_staged_lock_names_the_files_the_worktree_lacks(record, unstaged):
    stage(record.root, "gateseal.lock", OTHER_LOCK)
    for name in unstaged:
        (record.root / name).write_bytes(b"unstaged\n")
    message, step = refusal(record)
    assert all(name in message for name in unstaged)
    named = [name for name in ("gateseal.toml", "gateseal.lock") if name in step]
    assert named == list(unstaged)


@pytest.mark.parametrize("change", ["edited", "deleted", "linked copy"])
def test_an_unstaged_output_change_stops_the_commit(record, change):
    output = record.root / CONFIG
    output.unlink()
    if change == "edited":
        output.write_bytes(b"repos: [unstaged]\n")
    elif change == "linked copy":
        (record.root / "copy.yaml").write_bytes(OUTPUTS[CONFIG])
        output.symlink_to("copy.yaml")
    message, step = refusal(record)
    assert CONFIG in message
    assert CONFIG in step
    assert "gateseal.toml" not in step


def test_a_lock_blob_git_cannot_read_is_staged_again(record):
    (oid,) = blobs(record.root, "gateseal.lock")
    (record.root / ".git/objects" / oid[:2] / oid[2:]).unlink()
    message, step = refusal(record)
    assert "gateseal.lock" in message
    assert "git add" in step
    assert "gateseal.lock" in step


def test_the_record_belongs_to_the_worktree_it_was_prepared_for(
    record, tmp_path, monkeypatch
):
    monkeypatch.chdir(record.root)
    bootstrap.check_repository(record)
    moved = tmp_path / "moved"
    moved.mkdir()
    monkeypatch.chdir(moved)
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.check_repository(record)
    assert str(record.root) in str(raised.value)
    assert f"gateseal activate in {moved.resolve()}" in raised.value.next_step
