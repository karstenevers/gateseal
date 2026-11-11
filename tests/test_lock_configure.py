"""configure converges a worktree's tracked files on the selected policy.

These tests call lock.configure in-process on a plain directory with the
bundled policy and a compiler pinned by its URL, so neither Git nor the
network runs. The helpers here serve the other test_lock_* files too.
"""

import hashlib
import json
import stat
import zipfile

import pytest
from conftest import named_flags
from gateseal import __version__, lock
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_bundled_policy
from gateseal.validation import canonical

POLICY = load_bundled_policy()
WHEEL = f"gateseal-{__version__}-py3-none-any.whl"
SOURCE = (
    f"https://raw.githubusercontent.com/example/wheels/{'0' * 40}/artifacts/{WHEEL}"
)
CHECKSUM = "a" * 64
MANIFEST, LOCK, CONFIG = "gateseal.toml", "gateseal.lock", ".pre-commit-config.yaml"
RUFF, WORKFLOW = ".gateseal/ruff.toml", ".github/workflows/gateseal.yml"
CI_TABLE = '[ci]\nprovider = "github"\ndefault_branch = "main"\n\n'
OTHER_RELEASE = f"{int(__version__.split('.')[0]) + 1}.0.0"


def worktree(tmp_path):
    """Return an empty worktree root, with its preparation directory beside it."""
    root = tmp_path / "worktree"
    root.mkdir()
    (tmp_path / "preparation").mkdir()
    return root


def preparation(root):
    return root.parent / "preparation"


def configure(
    root,
    capability=None,
    request=None,
    *,
    wheel=None,
    source=None,
    checksum=None,
    ci=None,
    git="git",
):
    return lock.configure(
        root,
        git,
        preparation(root),
        capability,
        request or lock.PolicyRequest(),
        wheel,
        source,
        checksum,
        ci,
    )


def configured(tmp_path, *capability, ci=None):
    root = worktree(tmp_path)
    configure(
        root, list(capability or ("hygiene",)), source=SOURCE, checksum=CHECKSUM, ci=ci
    )
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


def other_compiler(document):
    """Make document's compiler pin name another gateseal release."""
    filename = WHEEL.replace(__version__, OTHER_RELEASE)
    document["compiler"].update(
        version=OTHER_RELEASE,
        filename=filename,
        source={"kind": "https-wheel", "location": SOURCE.replace(WHEEL, filename)},
    )


def sha(data):
    return hashlib.sha256(data).hexdigest()


def compiler_pin(kind="https-wheel", location=SOURCE, checksum=CHECKSUM):
    return {
        "name": "gateseal",
        "version": __version__,
        "filename": WHEEL,
        "sha256": checksum,
        "source": {"kind": kind, "location": location},
    }


def local_wheel(directory):
    """Write a wheel that names this compiler release, and return it and its hash."""
    path = directory / WHEEL
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(
            f"gateseal-{__version__}.dist-info/METADATA",
            f"Metadata-Version: 2.4\nName: gateseal\nVersion: {__version__}\n",
        )
    return path, sha(path.read_bytes())


def test_a_new_manifest_needs_an_explicit_capability(tmp_path):
    root = worktree(tmp_path)
    error = refused(configure, root, source=SOURCE, checksum=CHECKSUM)
    assert error.code == "missing_capability"
    assert named_flags(str(error)) == {"--capability"}
    assert all(name in str(error) for name in ("hygiene", "python", "secrets"))
    assert tracked(root) == {}


def test_configure_writes_the_manifest_lock_and_outputs(tmp_path):
    root = worktree(tmp_path)
    changed, notes = configure(
        root,
        ["python", "hygiene", "python"],
        source=SOURCE,
        checksum=CHECKSUM,
    )
    assert changed == (MANIFEST, LOCK, RUFF, CONFIG)
    files = tracked(root)
    assert sorted(files) == sorted(changed)
    assert files[MANIFEST].decode() == (
        'schema = 1\ncapabilities = ["hygiene", "python"]\n\n[policy]\n'
        f'source = "bundled"\nrelease = "{POLICY.release}"\n\n[choices]\n\n'
        "[exceptions]\n"
    )
    for name in changed:
        assert stat.S_IMODE((root / name).stat().st_mode) == 0o644, name
    document = json.loads(files[LOCK])
    assert document["compiler"] == compiler_pin()
    assert document["manifest_sha256"] == sha(files[MANIFEST])
    assert document["policy"] == {
        "kind": "bundled",
        "release": POLICY.release,
        "digest": POLICY.content_digest,
        "compiler_sha256": CHECKSUM,
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


def test_a_rerun_changes_nothing(tmp_path):
    root = configured(tmp_path, "hygiene", "python")
    before = tracked(root)
    assert configure(root)[0] == ()
    assert (
        configure(root, ["python", "hygiene"], source=SOURCE, checksum=CHECKSUM)[0]
        == ()
    )
    assert configure(root, request=lock.PolicyRequest("bundled"))[0] == ()
    assert tracked(root) == before


def test_configure_writes_the_workflow_of_the_selected_ci(tmp_path):
    root = worktree(tmp_path)
    changed, _ = configure(
        root, ["hygiene"], source=SOURCE, checksum=CHECKSUM, ci="github"
    )
    assert changed == (MANIFEST, LOCK, WORKFLOW, CONFIG)
    assert CI_TABLE in (root / MANIFEST).read_text()
    assert WHEEL in (root / WORKFLOW).read_text()
    assert configure(root, ci="github")[0] == ()


def test_a_local_wheel_serves_configure_but_not_ci(tmp_path):
    root = worktree(tmp_path)
    wheel, checksum = local_wheel(tmp_path)
    error = refused(
        configure, root, ["hygiene"], wheel=str(wheel), checksum=checksum, ci="github"
    )
    assert error.code == "ci_needs_network_compiler"
    assert named_flags(str(error)) == {"--compiler-wheel"}
    assert "gateseal configure" in error.next_step
    assert named_flags(error.next_step) == {
        "--compiler-source",
        "--compiler-sha256",
        "--compiler-wheel",
    }
    assert tracked(root) == {}
    configure(root, ["hygiene"], wheel=str(wheel), checksum=checksum)
    document = json.loads((root / LOCK).read_bytes())
    location = str(wheel.resolve())
    assert document["compiler"] == compiler_pin("local-wheel", location, checksum)


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


def test_compiler_flags_must_name_the_locked_compiler(tmp_path):
    root = configured(tmp_path)
    before = tracked(root)
    error = refused(configure, root, source=SOURCE, checksum="b" * 64)
    assert error.code == "configure_assertion_failed"
    error = refused(configure, root, checksum=CHECKSUM)
    assert error.code == "missing_artifact"
    assert named_flags(str(error)) == {
        "--compiler-wheel",
        "--compiler-source",
        "--compiler-sha256",
    }
    assert tracked(root) == before


@pytest.mark.parametrize(
    "data,code,detail",
    [
        (b"not json", "invalid_json", None),
        (canonical({"compiler": {}}), "invalid_field", "filename"),
    ],
    ids=["not json", "no pin"],
)
def test_an_unreadable_lock_names_the_compiler_pin(tmp_path, data, code, detail):
    root = configured(tmp_path)
    (root / LOCK).write_bytes(data)
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == code
    assert LOCK in str(error)
    assert detail is None or detail in str(error)
    assert LOCK in error.next_step
    assert "gateseal configure" in error.next_step
    assert named_flags(error.next_step) == {
        "--compiler-wheel",
        "--compiler-source",
        "--compiler-sha256",
    }
    assert tracked(root) == before


def test_another_compilers_lock_needs_that_compiler(tmp_path):
    root = configured(tmp_path)
    edit_lock(root, other_compiler)
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == "selected_compiler_required"
    assert f"gateseal-{OTHER_RELEASE}-py3-none-any.whl" in str(error)
    assert CHECKSUM in str(error)
    assert tracked(root) == before


def other_digest(document):
    document["policy"]["digest"] = "sha256:" + "0" * 64


def other_runtime(document):
    document["runtime"]["minimum_uv"] = "0.0.1"


@pytest.mark.parametrize("change", [other_digest, other_runtime])
def test_a_lock_from_another_build_of_this_release_needs_that_build(tmp_path, change):
    root = configured(tmp_path)
    edit_lock(root, change)
    before = tracked(root)
    error = refused(configure, root)
    assert error.code == "selected_compiler_required"
    assert WHEEL in str(error)
    assert CHECKSUM in str(error)
    assert tracked(root) == before


def test_a_lock_that_holds_only_the_compiler_pin_is_rewritten(tmp_path):
    root = configured(tmp_path)
    complete = (root / LOCK).read_bytes()
    (root / LOCK).write_bytes(canonical({"compiler": compiler_pin()}))
    assert configure(root)[0] == (LOCK,)
    assert (root / LOCK).read_bytes() == complete


def test_an_executable_manifest_is_refused(tmp_path):
    root = configured(tmp_path)
    (root / MANIFEST).chmod(0o755)
    error = refused(configure, root)
    assert error.code == "file_mode_mismatch"
    assert all(value in str(error) for value in (MANIFEST, "0755"))
