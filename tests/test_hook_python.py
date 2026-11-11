"""A capability offers alternatives, and a policy pins its Hook Python.

Each test writes the how-to's Black example into a copy of the bundled policy,
then runs gateseal policy lock, add, pin and check, or compiles what a
consumer's configure would, in process. A fake upstream serves tags and
release files where gateseal policy lock and add would reach GitHub.
"""

import json
import shutil
from pathlib import Path

import pytest
from black_policy import BLACK, BUILD, PYTHON, REVISION, SUMS, add_black
from gateseal import authoring, bootstrap, hook_python, upgrade
from gateseal.compiler_release import RELEASES, CompilerArtifact
from gateseal.diagnostics import GatesealError
from gateseal.lock import Locked, Snapshot, compile_lock, compile_outputs, describe
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import bundled_root, load_policy_tree
from gateseal.tools import built_in_tools
from gateseal.validation import canonical

CONFIG = ".pre-commit-config.yaml"
BLACK_CHOSEN = '\n[choices.python]\nformatter = "black"\n'


class Upstream:
    """The bundled policy's tags and checksum files, Black's tag and CPython's."""

    def __init__(self):
        lock = json.loads((bundled_root() / "policy.lock").read_bytes())
        self.tags, self.files = {(BLACK, "26.10.1"): REVISION}, {}
        for name, component in lock["components"].items():
            tool = built_in_tools()[name]
            for version, record in component["versions"].items():
                if "revision" in record:
                    tag = tool.source.tag.replace("{version}", version)
                    self.tags[tool.source.repository, tag] = record["revision"]
                    continue
                lines = []
                for archive in record["platforms"].values():
                    base, filename = archive["url"].rsplit("/", 1)
                    lines.append(f"{archive['sha256']}  {filename}")
                listing = tool.source.checksums.replace("{version}", version)
                self.files[f"{base}/{listing}"] = "\n".join(lines).encode()
        self.build(BUILD, SUMS)

    def build(self, build, sums):
        """Publish a python-build-standalone build and make it the newest."""
        self.files[f"{RELEASES}{build}/SHA256SUMS"] = sums.encode()
        self.files[hook_python.LATEST] = json.dumps({"tag": build}).encode()

    def tagged(self, repository, tag):
        return self.tags.get((repository, tag))

    def download(self, url):
        return self.files.get(url)


@pytest.fixture
def upstream(monkeypatch):
    fake = Upstream()
    monkeypatch.setattr(authoring, "Releases", lambda git, scratch, rerun: fake)
    return fake


def team(tmp_path, python=True, locked=True):
    """Copy the bundled policy and offer the how-to's Black alternative in it."""
    root = tmp_path / "policy"
    shutil.copytree(bundled_root(), root, ignore=shutil.ignore_patterns("*.md"))
    add_black(root, python=python, locked=locked)
    return root


def consumer(policy, capabilities=("hygiene", "python"), extra=""):
    """Return what configure compiles for capabilities: Lock state and Lock."""
    source = BundledSource(policy.release)
    manifest = render(capabilities, source) + extra.encode()
    wheel = "gateseal-0.1.0-py3-none-any.whl"
    artifact = CompilerArtifact("local-wheel", f"/{wheel}", wheel, "a" * 64, "0.1.0")
    selection = resolve(parse_manifest(manifest), policy)
    lock = compile_lock(manifest, source, policy, artifact, selection)
    outputs = compile_outputs(selection, artifact, source)
    snapshot = Snapshot(manifest, b"", outputs)
    return Locked(Path(), policy, artifact, snapshot, source, selection), lock


def hooks(locked):
    """Return each configured repository's hooks: their IDs and language_version."""
    config = json.loads(locked.snapshot.outputs[CONFIG])
    return {
        repository["repo"]: [
            (hook["id"], hook.get("language_version")) for hook in repository["hooks"]
        ]
        for repository in config["repos"]
    }


def messages(locked, category):
    return [note.message for note in describe(locked) if note.category == category]


def failure(call, *arguments):
    with pytest.raises(GatesealError) as raised:
        call(*arguments)
    return raised.value


def test_the_default_alternative_runs_ruff_format_and_prepares_no_black(tmp_path):
    locked, lock = consumer(load_policy_tree(team(tmp_path, python=False)))
    assert hooks(locked) == {
        "https://github.com/pre-commit/pre-commit-hooks": [
            ("trailing-whitespace", None),
            ("end-of-file-fixer", None),
            ("check-yaml", None),
        ],
        "https://github.com/astral-sh/ruff-pre-commit": [
            ("ruff-check", None),
            ("ruff-format", None),
        ],
    }
    assert "black" not in lock["components"]
    assert ".gateseal/black.toml" not in locked.snapshot.outputs
    assert "Choice python.formatter: ruff-format." in messages(locked, "policy_choice")
    assert messages(locked, "hook_python") == []


def test_a_repository_that_picks_black_runs_it_on_the_hook_python(tmp_path):
    root = team(tmp_path)
    locked, lock = consumer(load_policy_tree(root), extra=BLACK_CHOSEN)
    assert hooks(locked) == {
        "https://github.com/pre-commit/pre-commit-hooks": [
            ("trailing-whitespace", "python3.15.2"),
            ("end-of-file-fixer", "python3.15.2"),
            ("check-yaml", "python3.15.2"),
        ],
        "https://github.com/astral-sh/ruff-pre-commit": [
            ("ruff-check", "python3.15.2"),
        ],
        BLACK: [("black", "python3.15.2")],
    }
    assert lock["components"]["black"]["revision"] == REVISION
    assert lock["components"]["python"] == {"version": "3.15.2", "python": PYTHON}
    rules = (root / "rules/black.toml").read_bytes()
    assert locked.snapshot.outputs[".gateseal/black.toml"] == rules
    assert "Choice python.formatter: black." in messages(locked, "policy_choice")
    assert messages(locked, "hook_python") == [
        "Python-language hooks run on the policy's Hook Python, CPython 3.15.2 "
        f"build {BUILD}."
    ]
    assert (
        "Tool black 26.10.1 for python: team-defined, pre-commit-repository; it "
        "carries the tool-specific guarantees only as far as its definition's "
        "arguments declare them." in messages(locked, "tool")
    )


def test_release_binaries_never_name_the_hook_python(tmp_path):
    locked, _ = consumer(load_policy_tree(team(tmp_path)), ("hygiene", "secrets"))
    config = json.loads(locked.snapshot.outputs[CONFIG])
    local = [
        repository for repository in config["repos"] if repository["repo"] == "local"
    ]
    assert local and all(
        "language_version" not in hook for repo in local for hook in repo["hooks"]
    )


def test_upgrade_reports_a_hook_python_that_one_release_adds(tmp_path):
    _, runtime = consumer(load_policy_tree(team(tmp_path / "v1", python=False)))
    _, pinned = consumer(load_policy_tree(team(tmp_path / "v2")))
    moved = {e["target"]: e for e in upgrade.report(runtime, pinned, frozenset())}
    assert moved["hook-python"]["message"] == "Hook Python 3.15.2: added."
    assert (moved["hook-python"]["from"], moved["hook-python"]["to"]) == (
        None,
        "3.15.2",
    )
    back = {e["target"]: e for e in upgrade.report(pinned, runtime, frozenset())}
    assert back["hook-python"]["message"] == "Hook Python 3.15.2: removed."
    assert back["ruff"]["state"] == "unchanged"


def test_a_choice_names_one_of_the_alternatives(tmp_path):
    policy = load_policy_tree(team(tmp_path))
    error = failure(
        consumer,
        policy,
        ("hygiene", "python"),
        BLACK_CHOSEN.replace("black", "ruff-check"),
    )
    assert error.code == "invalid_choice"
    assert "takes one of ruff-format, black, not 'ruff-check'" in str(error)


def test_an_exception_for_an_unchosen_alternative_is_unused(tmp_path):
    exception = (
        '\n[exceptions.black]\nversion = "26.10.1"\nreason = "r"\n'
        "review_by = 2026-12-31\n"
    )
    error = failure(
        consumer, load_policy_tree(team(tmp_path)), ("hygiene", "python"), exception
    )
    assert error.code == "unused_exception"
    assert "alternatives do not use" in str(error)


@pytest.mark.parametrize(
    "old,new,code,message",
    [
        (
            'alternatives.formatter = ["ruff-format", "black"]',
            'alternatives.formatter = ["ruff-format", "isort"]',
            "unsupported_hook",
            "offers isort, which no tool of python runs",
        ),
        (
            'alternatives.formatter = ["ruff-format", "black"]',
            'alternatives.formatter = ["black"]',
            "invalid_field",
            "two or more distinct hook IDs",
        ),
        (
            'alternatives.formatter = ["ruff-format", "black"]',
            'alternatives.formatter = ["ruff-format", "black"]\n'
            'alternatives.style = ["black", "ruff-check"]',
            "duplicate_identifier",
            "offers black, which capabilities.python.alternatives.formatter",
        ),
        (
            '[choices.python.formatter]\ndefault = "ruff-format"\n',
            "",
            "invalid_field",
            "needs choices.python.formatter with the default hook",
        ),
        (
            'alternatives.formatter = ["ruff-format", "black"]',
            'alternatives.target_version = ["ruff-format", "black"]',
            "invalid_field",
            "declares the choice target_version",
        ),
    ],
)
def test_alternatives_are_validated_with_the_policy(tmp_path, old, new, code, message):
    root = team(tmp_path)
    text = (root / "policy.toml").read_text()
    assert old in text
    (root / "policy.toml").write_text(text.replace(old, new))
    error = failure(load_policy_tree, root)
    assert error.code == code
    assert message in str(error)


def test_policy_add_and_pin_review_the_hook_python(tmp_path, upstream):
    root = team(tmp_path, python=False)
    assert authoring.lock(root).code == "locked"
    added = authoring.add(root, "python", "3.15.2")
    assert (added.code, added.changed) == ("added", ("policy.toml", "policy.lock"))
    assert added.next_steps == ()
    text = (root / "policy.toml").read_text()
    assert text.endswith(
        '[components.black.versions."26.10.1"]\n\n'
        '[components.python]\npin = "3.15.2"\n\n'
        '[components.python.versions."3.15.2"]\n'
    )
    lock = json.loads((root / "policy.lock").read_bytes())
    assert lock["components"]["python"] == {"versions": {"3.15.2": PYTHON}}
    upstream.build(
        "20261201", SUMS.replace("3.15.2", "3.15.3").replace(BUILD, "20261201")
    )
    assert authoring.add(root, "python", "3.15.3").code == "added"
    assert authoring.pin(root, "python", "3.15.3").message == (
        "policy.toml pins python 3.15.3. 3.15.2 stays reviewed."
    )
    assert authoring.lock(root).changed == ()
    assert load_policy_tree(root).hook_python.version == "3.15.3"
    assert authoring.check(root).code == "consistent"


def test_a_version_the_newest_build_lacks_is_refused_with_a_step(tmp_path, upstream):
    root = team(tmp_path, python=False)
    authoring.lock(root)
    error = failure(authoring.add, root, "python", "3.15.1")
    assert error.code == "upstream_archive_missing"
    assert "carries only the newest patch release" in str(error)
    assert error.next_step.startswith("Check that python 3.15.1 is released")
    assert (
        "python" not in (root / "policy.toml").read_text().split("[components.black")[1]
    )


def test_a_recorded_digest_is_checked_against_its_own_build(tmp_path, upstream):
    root = team(tmp_path)
    upstream.build("20261201", SUMS.replace(BUILD, "20261201"))
    assert authoring.lock(root).changed == ()
    upstream.files[f"{RELEASES}{BUILD}/SHA256SUMS"] = SUMS.replace(
        "1b1b", "2b1b"
    ).encode()
    assert failure(authoring.lock, root).code == "upstream_changed"


def test_a_pin_of_the_runtimes_cpython_runs_hooks_on_the_runtime(tmp_path):
    root = team(tmp_path)
    for name in ("policy.toml", "policy.lock"):
        path = root / name
        path.write_text(
            path.read_text()
            .replace("3.15.2", "3.14.10")
            .replace("cpython-3.15", "cpython-3.14")
        )
    lock = json.loads((root / "policy.lock").read_bytes())
    for record in lock["components"]["python"]["versions"]["3.14.10"].values():
        record["minor"], record["patch"] = 14, 10
    (root / "policy.lock").write_bytes(canonical(lock))
    locked, lock = consumer(load_policy_tree(root), extra=BLACK_CHOSEN)
    assert {version for hooks in hooks(locked).values() for _, version in hooks} == {
        None
    }
    assert "python" not in lock["components"]
    assert messages(locked, "hook_python") == [
        "The policy's Hook Python, CPython 3.14.10, is this gateseal release's own "
        "CPython, so Python-language hooks run on the Compiler Runtime."
    ]


def test_a_lock_record_of_another_cpython_is_an_edit(tmp_path):
    root = team(tmp_path)
    lock = json.loads((root / "policy.lock").read_bytes())
    records = lock["components"]["python"]["versions"]["3.15.2"]
    lock["components"]["python"]["versions"]["3.15.2"] = {
        key.replace("3.15.2", "3.15.3"): {**record, "patch": 3}
        for key, record in records.items()
    }
    (root / "policy.lock").write_bytes(canonical(lock))
    error = failure(load_policy_tree, root)
    assert error.code == "policy_lock_edited"
    assert "records CPython 3.15.3" in str(error)


def prepared(tmp_path, hook_python):
    """Write an attempt with every prepared file, and return its record."""
    attempt = tmp_path / ("attempt-" + "a" * 32)
    trees = []
    for name in ("store", "hook store", "compiler", "pre-commit"):
        (tmp_path / name / "bin").mkdir(parents=True)
        (tmp_path / name / "bin" / "python").write_text(name)
        trees.append(tmp_path / name)
    paths = bootstrap.PreparedPaths(
        git=Path(shutil.which("git")),
        interpreter=tmp_path / "store/bin/python",
        compiler_python=tmp_path / "compiler/bin/python",
        pre_commit_python=tmp_path / "pre-commit/bin/python",
        bootstrap=tmp_path / "compiler/bin/python",
        pre_commit_home=attempt / "pre-commit-home",
        tools=attempt / "tools",
    )
    paths.tools.mkdir(parents=True)
    (attempt / "hook").mkdir()
    (attempt / "hook/python").symlink_to(paths.interpreter)
    (attempt / "hook/bootstrap.py").symlink_to(paths.bootstrap)
    tool = None
    if hook_python:
        tool = bootstrap.PreparedTool("3.15.2", tmp_path / "hook store/bin/python")
        (paths.tools / "python3.15.2").symlink_to(tool.executable)
    else:
        trees.remove(tmp_path / "hook store")
    roots = tuple(trees)
    return bootstrap.PreparedRecord(
        attempt=attempt,
        root=tmp_path,
        lock_sha256="0" * 64,
        platform=bootstrap.platform.system() + "-" + bootstrap.platform.machine(),
        paths=paths,
        outputs=(),
        native_tools={},
        hook_python=tool,
        trees={
            root: bootstrap.TreeDigests(
                bootstrap.tree_digest(root, roots, content=False),
                bootstrap.tree_digest(root, roots, content=True),
            )
            for root in roots
        },
        hook_environments={},
        replaces=None,
    )


@pytest.mark.parametrize("hook_python", [True, False])
def test_a_commit_refuses_a_hook_python_link_the_record_does_not_name(
    tmp_path, hook_python
):
    record = prepared(tmp_path, hook_python)
    bootstrap.verify_runtime(record, content=True)
    document = record.document()
    assert document["hook_python"] == (
        {"version": "3.15.2", "executable": str(tmp_path / "hook store/bin/python")}
        if hook_python
        else None
    )
    link = record.paths.tools / "python3.15.2"
    if hook_python:
        link.unlink()
        link.symlink_to(record.paths.interpreter)
    else:
        link.symlink_to(record.paths.interpreter)
    with pytest.raises(bootstrap.BootstrapError, match="changed"):
        bootstrap.verify_runtime(record, content=False)
