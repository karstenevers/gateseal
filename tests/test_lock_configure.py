"""configure converges a worktree's tracked files on the selected policy.

These tests call lock.configure in-process on a plain directory with the
bundled policy and conftest's PIN for gateseal's release tag, so neither Git
nor the network runs. The helpers here serve the other test_lock_* files too.
"""

import hashlib
import json
import stat

import pytest
from conftest import PIN, named_flags
from gateseal import __version__, lock
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_bundled_policy
from gateseal.validation import canonical

POLICY = load_bundled_policy()
MANIFEST, LOCK, CONFIG = "gateseal.toml", "gateseal.lock", ".pre-commit-config.yaml"
RUFF, WORKFLOW = ".gateseal/ruff.toml", ".github/workflows/gateseal.yml"
CI_TABLE = '\n[ci]\nprovider = "github"\ndefault_branch = "main"\n'
OTHER_RELEASE = f"{int(__version__.split('.')[0]) + 1}.0.0"


def worktree(tmp_path):
    """Return an empty worktree root, with its preparation directory beside it."""
    root = tmp_path / "worktree"
    root.mkdir()
    (tmp_path / "preparation").mkdir()
    return root


def preparation(root):
    return root.parent / "preparation"


def configure(root, capability=None, request=None, *, ci=None, git="git"):
    return lock.configure(
        root, git, preparation(root), capability, request or lock.PolicyRequest(), ci
    )


def configured(tmp_path, *capability, ci=None):
    root = worktree(tmp_path)
    configure(root, list(capability or ("hygiene",)), ci=ci)
    return root


def tracked(root):
    """Return every file under root by its relative name."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def check_policy_note(note):
    """Check that note names the bundled release, its gateseal version and digest."""
    assert (note.severity, note.category) == ("info", "policy")
    named = (POLICY.release, __version__, POLICY.content_digest)
    assert all(value in note.message for value in named)


def refused(call, *arguments, **options):
    with pytest.raises(GatesealError) as failure:
        call(*arguments, **options)
    return failure.value


def outcome(error):
    """Return what a caller acts on: the error's code and its next step."""
    return error.code, error.next_step


def edit(root, name, change):
    path = root / name
    path.write_text(change(path.read_text()))


def edit_lock(root, change):
    path = root / LOCK
    document = json.loads(path.read_bytes())
    change(document)
    path.write_bytes(canonical(document))


def other_gateseal(document):
    """Make document's gateseal pin name another gateseal release."""
    document["gateseal"]["version"] = OTHER_RELEASE


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_a_new_manifest_needs_an_explicit_capability(tmp_path):
    root = worktree(tmp_path)
    error = refused(configure, root)
    assert error.code == "missing_capability"
    assert named_flags(str(error)) == {"--capability"}
    assert all(name in str(error) for name in ("hygiene", "python", "secrets"))
    assert tracked(root) == {}


def test_configure_writes_the_manifest_lock_and_outputs(tmp_path):
    root = worktree(tmp_path)
    changed, notes = configure(root, ["python", "hygiene", "python"])
    assert changed == (MANIFEST, LOCK, RUFF, CONFIG)
    files = tracked(root)
    assert sorted(files) == sorted(changed)
    assert files[MANIFEST].decode() == (
        'schema = 1\ncapabilities = ["hygiene", "python"]\n\n[policy]\n'
        f'source = "bundled"\nrelease = "{POLICY.release}"\n'
    )
    for name in changed:
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o644, name
    document = json.loads(files[LOCK])
    assert (document["schema"], document["gateseal"]) == (2, PIN.document())
    assert document["manifest_sha256"] == sha(files[MANIFEST])
    assert document["policy"] == {
        "kind": "bundled",
        "release": POLICY.release,
        "digest": POLICY.content_digest,
    }
    assert document["outputs"] == {
        name: {"sha256": sha(files[name]), "encoding": "utf-8", "executable": False}
        for name in (RUFF, CONFIG)
    }
    check_policy_note(notes[0])
    assert [note.category for note in notes[2:]] == [
        "tool",
        "tool",
        "policy_choice",
        "policy_choice",
    ]


def test_configure_selects_secrets_alone(tmp_path):
    root = worktree(tmp_path)
    changed, _ = configure(root, ["secrets"])
    assert 'capabilities = ["secrets"]' in (root / MANIFEST).read_text()
    outputs = set(changed) - {MANIFEST, LOCK, CONFIG}
    assert outputs and all(name.startswith(".gateseal/betterleaks") for name in outputs)
    config = json.loads((root / CONFIG).read_bytes())
    hooks = [hook["id"] for repo in config["repos"] for hook in repo["hooks"]]
    assert hooks == ["gateseal-check", "betterleaks"]


def test_a_rerun_changes_nothing(tmp_path):
    root = configured(tmp_path, "hygiene", "python")
    before = tracked(root)
    assert configure(root)[0] == ()
    assert configure(root, ["python", "hygiene"])[0] == ()
    assert configure(root, request=lock.PolicyRequest("bundled"))[0] == ()
    assert tracked(root) == before


def test_configure_writes_the_workflow_of_the_selected_ci(tmp_path):
    root = worktree(tmp_path)
    changed, _ = configure(root, ["hygiene"], ci="github")
    assert changed == (MANIFEST, LOCK, WORKFLOW, CONFIG)
    assert CI_TABLE in (root / MANIFEST).read_text()
    assert PIN.commit in (root / WORKFLOW).read_text()
    assert configure(root, ci="github")[0] == ()


def test_only_a_new_lock_reads_the_release_tag(tmp_path, monkeypatch):
    root = configured(tmp_path)
    before = tracked(root)

    def unreachable(git, preparation):
        raise AssertionError("configure read the release tag again")

    monkeypatch.setattr(lock, "release_pin", unreachable)
    assert configure(root)[0] == ()
    assert tracked(root) == before


@pytest.mark.parametrize(
    "options,named,table",
    [
        ({"capability": ["python"]}, {"hygiene"}, "capabilities"),
        (
            {"request": lock.PolicyRequest("https://example.com/team/policy.git")},
            {"--policy"},
            "[policy]",
        ),
        ({"request": lock.PolicyRequest(tag="v1")}, {"--policy-tag"}, "[policy]"),
        ({"ci": "github"}, set(), "[ci]"),
    ],
    ids=["capability", "policy", "policy tag", "ci"],
)
def test_flags_must_match_the_existing_manifest(tmp_path, options, named, table):
    root = configured(tmp_path)
    before = tracked(root)
    error = refused(configure, root, **options)
    assert error.code == "configure_assertion_failed"
    assert all(token in str(error) for token in {MANIFEST, *named})
    assert named_flags(str(error)) == {t for t in named if t.startswith("--")}
    assert table in error.next_step
    assert "gateseal configure" in error.next_step
    assert tracked(root) == before


def test_the_ci_flag_must_name_the_manifests_provider(tmp_path):
    root = configured(tmp_path, ci="github")
    error = refused(configure, root, ci="gitlab")
    assert error.code == "configure_assertion_failed"
    assert all(value in str(error) for value in (MANIFEST, "github"))


@pytest.mark.parametrize(
    "data,code,detail",
    [
        (b"not json", "invalid_json", None),
        (canonical({"schema": 2, "gateseal": {}}), "invalid_field", "commit"),
        (
            canonical({"schema": 2, "gateseal": {**PIN.document(), "commit": "a1"}}),
            "invalid_field",
            "commit",
        ),
    ],
    ids=["not json", "no pin", "short commit"],
)
def test_an_unreadable_lock_names_the_way_out(tmp_path, data, code, detail):
    root = configured(tmp_path)
    (root / LOCK).write_bytes(data)
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == code
    assert LOCK in str(error)
    assert detail is None or detail in str(error)
    assert LOCK in error.next_step
    assert "gateseal configure" in error.next_step
    assert tracked(root) == before


@pytest.mark.parametrize(
    "schema,step",
    [
        (1, "Delete gateseal.lock and the files gateseal wrote"),
        (3, "Install the newer gateseal that wrote gateseal.lock."),
    ],
    ids=["older format", "newer format"],
)
def test_a_lock_of_another_format_is_refused_first(tmp_path, schema, step):
    root = configured(tmp_path)
    (root / LOCK).write_bytes(canonical({"schema": schema, "compiler": "0.1.0"}))
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == "unsupported_schema"
    assert error.next_step.startswith(step)
    assert tracked(root) == before


def test_another_releases_lock_names_gateseal_upgrade(tmp_path):
    root = configured(tmp_path)
    edit_lock(root, other_gateseal)
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == "selected_compiler_required"
    assert all(value in str(error) for value in (OTHER_RELEASE, PIN.commit))
    assert "gateseal upgrade" in error.next_step
    assert tracked(root) == before


def test_a_lock_from_another_build_of_this_release_needs_that_build(tmp_path):
    root = configured(tmp_path)
    edit_lock(root, lambda d: d["policy"].update(digest="sha256:" + "0" * 64))
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == "selected_compiler_required"
    assert all(value in error.next_step for value in (PIN.commit, PIN.repository))
    assert tracked(root) == before


def test_a_lock_that_holds_only_the_ledger_pin_is_rewritten(tmp_path):
    root = configured(tmp_path)
    complete = (root / LOCK).read_bytes()
    (root / LOCK).write_bytes(canonical({"schema": 2, "gateseal": PIN.document()}))
    assert configure(root)[0] == (LOCK,)
    assert (root / LOCK).read_bytes() == complete


def test_an_executable_manifest_is_refused(tmp_path):
    root = configured(tmp_path)
    (root / MANIFEST).chmod(0o755)
    error = refused(configure, root)
    assert error.code == "file_mode_mismatch"
    assert all(value in str(error) for value in (MANIFEST, "0755"))
