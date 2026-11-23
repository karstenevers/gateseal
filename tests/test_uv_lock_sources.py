"""uv.lock must not install a tool's package from the project's own source.

uv skips a tool's --with layer when the project's environment already holds
the package at that version, so the project's package would run in place of
the reviewed release. configure and the commit check refuse such a uv.lock.
"""

import json

import pytest
from conftest import BUNDLED_TOOLS, TRACKED, stderr_refusal
from gateseal import commit_check, lock
from gateseal.diagnostics import GatesealError
from gateseal.tools.uv_project import refuse_shadowing
from gateseal.worktree import locate_worktree
from installation import clone, success
from stand_ins import configure
from test_lock_configure import configure as configure_root
from test_lock_configure import refused, tracked, worktree

PIN = BUNDLED_TOOLS["pyright"].pin
PACKAGES = {"pyright": ("pyright[nodejs]", PIN)}
PATH = '{ path = "vendor/pyright" }'
REGISTRY = '{ registry = "https://pypi.org/simple" }'
ROOT = """\
version = 1
revision = 3
requires-python = ">=3.12"

[[package]]
name = "shop"
version = "0"
source = { virtual = "." }
"""


def uv_lock(name="pyright", version=PIN, source=PATH):
    """Return a uv.lock whose second package is name at version from source."""
    return (
        ROOT
        + f'\n[[package]]\nname = "{name}"\nversion = "{version}"\n'
        + (f"source = {source}\n" if source else "")
    )


def message(location, version=PIN, kind="path", distribution="pyright", tool=None):
    shown = "" if location is None else f" {location!r}"
    return (
        f"uv.lock installs {distribution} {version} from its {kind} source{shown}, "
        "so the project's own package would run in place of the reviewed "
        f"{tool or distribution}."
    )


def next_step(distribution="pyright"):
    return (
        f"Install {distribution} from a package index, or give the project's own "
        f"{distribution} another version. Then run uv lock."
    )


def shadowed(text, packages=PACKAGES):
    with pytest.raises(GatesealError) as failure:
        refuse_shadowing(packages, text.encode())
    assert failure.value.code == "shadowed_tool"
    return str(failure.value), failure.value.next_step


@pytest.mark.parametrize(
    ("source", "kind", "location"),
    [
        (PATH, "path", "vendor/pyright"),
        ('{ editable = "packages/pyright" }', "editable", "packages/pyright"),
        ('{ git = "https://x:secret@example.invalid/pyright.git" }', "git", None),
        ('{ url = "https://example.invalid/p\'y.whl?sig=secret" }', "url", None),
        ('{ path = "vendor/py\\nright" }', "path", "vendor/py\nright"),
    ],
    ids=["path", "editable", "git", "url", "line break"],
)
def test_a_tool_s_package_at_its_pin_from_another_source_stops(source, kind, location):
    assert shadowed(uv_lock(source=source)) == (
        message(location, kind=kind),
        next_step(),
    )


@pytest.mark.parametrize(
    "version",
    [f"{PIN}+local", f"{PIN}.0", f"{PIN}.0.0", f"0!{PIN}", f"0!{PIN}.0+vendored.2"],
)
def test_a_version_that_satisfies_the_pin_stops(version):
    assert shadowed(uv_lock(version=version))[0] == message("vendor/pyright", version)


def test_a_pin_with_trailing_zeros_matches_a_shorter_release():
    packages = {"zero": ("zero", "0.0.0"), "two": ("two", "2.0.0")}
    assert shadowed(uv_lock("two", "2"), packages)[0] == message(
        "vendor/pyright", "2", distribution="two"
    )
    assert shadowed(uv_lock("zero", "0.0"), packages)[0] == message(
        "vendor/pyright", "0.0", distribution="zero"
    )


@pytest.mark.parametrize(
    "version",
    [
        f"{PIN}.post1",
        f"{PIN}rc1",
        f"{PIN}.dev0",
        f"{PIN}.1",
        f"1!{PIN}",
        f"1{PIN}",
        f"{PIN}0",
        "1.1",
        "",
    ],
)
def test_another_version_passes(version):
    refuse_shadowing(PACKAGES, uv_lock(version=version).encode())


def test_names_compare_as_pep_503_normalizes_them():
    packages = {"bar": ("Foo_Bar[fast]", "2.0.0"), "pyright": PACKAGES["pyright"]}
    assert shadowed(uv_lock("foo-bar", "2.0.0"), packages) == (
        message("vendor/pyright", "2.0.0", distribution="Foo_Bar", tool="bar"),
        next_step("Foo_Bar"),
    )
    assert shadowed(uv_lock("foo.._-bar", "2.0.0"), packages)[0].startswith(
        "uv.lock installs Foo_Bar 2.0.0"
    )
    refuse_shadowing(packages, uv_lock("foobar", "2.0.0").encode())
    refuse_shadowing(packages, uv_lock("pyright-mirror").encode())


@pytest.mark.parametrize(
    "text",
    [
        uv_lock(source=REGISTRY),
        uv_lock(source=REGISTRY.replace("}", ', path = "vendor" }')),
        uv_lock(source='{ virtual = "." }'),
        uv_lock(source="{}"),
        uv_lock(source=None),
        uv_lock(source='"vendor/pyright"'),
        ROOT.replace('name = "shop"', "name = 1")
        + uv_lock()[len(ROOT) :].replace('name = "pyright"', "name = 1"),
        uv_lock().replace(f'version = "{PIN}"', "version = 1"),
        "package = [1]\n",
        "version = 1\n",
        "not TOML [",
        "",
    ],
    ids=[
        "registry",
        "registry and path",
        "virtual",
        "empty source",
        "no source",
        "source not a table",
        "name not a string",
        "version not a string",
        "package not a table",
        "no package array",
        "not TOML",
        "empty",
    ],
)
def test_a_uv_lock_without_a_shadowing_package_passes(text):
    refuse_shadowing(PACKAGES, text.encode())


def test_no_tool_checks_nothing():
    refuse_shadowing({}, uv_lock().encode())


def test_configure_refuses_a_linked_uv_lock_before_it_writes(tmp_path):
    root = worktree(tmp_path)
    (root / "pyproject.toml").write_text("")
    (root / "locks").mkdir()
    (root / "locks/uv.lock").write_text(uv_lock(source=REGISTRY))
    (root / "uv.lock").symlink_to("locks/uv.lock")
    before = tracked(root)
    failure = refused(configure_root, root, ["hygiene", "python-types"])
    assert (failure.code, str(failure), failure.next_step) == (
        "missing_project",
        "pyright runs in the repository's uv project, but uv.lock is a link, and "
        "the commit check reads only a regular file.",
        "Replace uv.lock with the file that it links to, or stop selecting "
        "python-types in gateseal.toml. Then run gateseal configure.",
    )
    assert tracked(root) == before


def test_configure_refuses_a_shadowed_tool_before_it_writes(tmp_path):
    root = worktree(tmp_path)
    (root / "pyproject.toml").write_text("")
    (root / "uv.lock").write_text(uv_lock())
    before = tracked(root)
    failure = refused(configure_root, root, ["hygiene", "python-types"])
    assert (failure.code, str(failure), failure.next_step) == (
        "shadowed_tool",
        message("vendor/pyright"),
        next_step(),
    )
    assert tracked(root) == before
    (root / "uv.lock").write_text(uv_lock(source=REGISTRY))
    configure_root(root, ["hygiene", "python-types"])
    (root / "uv.lock").write_text(uv_lock(source='{ directory = "vendor" }'))
    stopped = refused(lock.load, root, root.parent / "preparation")
    assert (stopped.code, str(stopped)) == (
        "shadowed_tool",
        message("vendor", kind="directory"),
    )


@pytest.fixture
def staged(tmp_path, monkeypatch):
    """A clone that stages what configure wrote for python-types, as the cwd."""
    root = clone(tmp_path / "origin", tmp_path / "consumer ü")
    (root / "pyproject.toml").write_text("")
    (root / "uv.lock").write_text(uv_lock(source=REGISTRY))
    configure(locate_worktree(root), ["hygiene", "python-types"])
    success(["git", "add", *TRACKED, ".gateseal", "pyproject.toml", "uv.lock"], root)
    monkeypatch.chdir(root)
    return root


NO_UV_LOCK = (
    "This commit has no uv.lock as a regular file, which uv reads to run pyright.",
    "Run uv lock if the project has none. Stage uv.lock as a regular file, then "
    "commit again.",
)


def hook(capsys):
    code = commit_check.main()
    err = capsys.readouterr().err
    return code, stderr_refusal(err) if code else err


def test_the_commit_check_reads_the_staged_uv_lock(staged, capsys):
    assert hook(capsys) == (0, "")
    (staged / "uv.lock").write_text(uv_lock())
    assert hook(capsys) == (0, "")
    success(["git", "add", "uv.lock"], staged)
    (staged / "uv.lock").write_text(uv_lock(source=REGISTRY))
    assert hook(capsys) == (1, (message("vendor/pyright"), next_step()))


def test_a_staged_uv_lock_that_is_not_toml_stops(staged, capsys):
    (staged / "uv.lock").write_text(f"<<<<<<< HEAD\n{uv_lock()}")
    success(["git", "add", "uv.lock"], staged)
    assert hook(capsys) == (
        1,
        (
            "This commit's uv.lock is not TOML, so gateseal cannot check where it "
            "installs pyright from.",
            "Run uv lock. Stage uv.lock, then commit again.",
        ),
    )


def test_a_staged_link_stops_also_when_its_target_s_name_is_toml(staged, capsys):
    (staged / "a=1").write_text(uv_lock())
    (staged / "uv.lock").unlink()
    (staged / "uv.lock").symlink_to("a=1")
    success(["git", "add", "uv.lock"], staged)
    assert hook(capsys) == (1, NO_UV_LOCK)


def test_a_commit_without_uv_lock_stops_also_when_the_worktree_has_one(staged, capsys):
    success(["git", "rm", "--cached", "--quiet", "uv.lock"], staged)
    assert (staged / "uv.lock").is_file()
    assert hook(capsys) == (1, NO_UV_LOCK)


@pytest.mark.parametrize("capabilities", [["hygiene"], ["hygiene", "typescript"]])
def test_a_lock_without_a_project_tool_reads_no_uv_lock(
    tmp_path, capsys, monkeypatch, capabilities
):
    root = clone(tmp_path / "origin", tmp_path / "consumer")
    configure(locate_worktree(root), capabilities)
    (root / "uv.lock").write_text(uv_lock())
    success(["git", "add", "-A"], root)
    monkeypatch.chdir(root)
    assert hook(capsys) == (0, "")


def edit_record(root, change):
    """Stage a Lock whose pyright record is what change returns for it."""
    document = json.loads((root / "gateseal.lock").read_text())
    components = document["components"]
    components["pyright"] = change(components["pyright"])
    (root / "gateseal.lock").write_text(json.dumps(document))
    success(["git", "add", "gateseal.lock"], root)


@pytest.mark.parametrize(
    ("change", "shown"),
    [
        (
            lambda record: record | {"extra": 1},
            "components.pyright requires exactly hook_ids, package, version.",
        ),
        (
            lambda record: record | {"package": 1},
            "components.pyright.package must be a nonempty printable string.",
        ),
        (
            lambda record: record | {"version": ""},
            "components.pyright.version must be a nonempty printable string.",
        ),
        (
            lambda record: record | {"version": "latest"},
            "components.pyright.version must be a stable major.minor.patch release.",
        ),
        (lambda record: 1, "components.pyright must be an object."),
    ],
    ids=["extra field", "package", "version", "unstable version", "not an object"],
)
def test_an_unreadable_project_tool_record_names_its_field(
    staged, capsys, change, shown
):
    edit_record(staged, change)
    assert hook(capsys)[1][0] == f"Cannot read gateseal.lock: {shown}"
