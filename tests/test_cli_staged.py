"""The prepared compiler's _staged step, which a commit's bootstrap execs.

bootstrap.main hands _staged a published attempt and the staged blob IDs of
the Manifest, the Lock and each output, in the record's order. _staged checks
them and then replaces itself with the attempt's pre-commit. The tests record a
real gateseal configuration's staged blobs and write the attempt's record by
hand; os.execve stands in for pre-commit, because exec would replace pytest.
"""

import contextlib
import io
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from conftest import stderr_refusal
from gateseal import bootstrap
from gateseal.cli import main
from installation import clone, success
from test_cli_commands import SHA, SOURCE


@pytest.fixture(scope="module")
def staged(tmp_path_factory):
    """A consumer whose configuration is staged, and its outputs' names."""
    root = tmp_path_factory.mktemp("cli staged")
    consumer = clone(root / "origin", root / "consumer")
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        configured = main(
            ["--repo", str(consumer), "configure", "--capability", "hygiene"]
            + ["--compiler-source", SOURCE, "--compiler-sha256", SHA]
        )
    assert configured == 0, output.getvalue()
    success(["git", "add", "-A"], consumer)
    outputs = list(json.loads((consumer / "gateseal.lock").read_bytes())["outputs"])
    return consumer, outputs


def blob(root, name):
    return success(["git", "rev-parse", f":{name}"], root).stdout.strip()


def stored(root, data):
    """Write data to root's object store and return its blob ID."""
    path = root / "blob"
    path.write_bytes(data)
    return success(["git", "hash-object", "-w", path.name], root).stdout.strip()


def attempt(directory, root, outputs, git):
    """Write a published attempt's record whose files live under directory."""
    path = directory / ("attempt-" + uuid.uuid4().hex)
    path.mkdir(parents=True)
    store = directory / "store"
    paths = {
        name: str(store / name)
        for name in (
            "interpreter",
            "compiler_python",
            "pre_commit_python",
            "bootstrap",
            "pre_commit_home",
            "tools",
        )
    }
    environment = store / "hooks" / "pre-commit-hooks"
    seals = {str(store / name): {"metadata": "m", "content": "c"} for name in "abc"}
    record = {
        "schema": 1,
        "root": str(root),
        "lock_sha256": "0" * 64,
        "platform": "Linux-x86_64",
        "paths": paths | {"git": str(git)},
        "outputs": outputs,
        "native_tools": {},
        "hook_python": None,
        "trees": seals | {str(environment.parent): {"metadata": "m", "content": "c"}},
        "hook_environments": {
            str(environment): {
                "component": "pre-commit-hooks",
                "python": str(environment / "bin" / "python"),
                "marker_sha256": "0" * 64,
                "executables": [],
            }
        },
        "replaces": None,
    }
    (path / "prepared.json").write_text(json.dumps(record))
    return path, {name: Path(value) for name, value in paths.items()}


@pytest.fixture
def published(staged, tmp_path):
    root, outputs = staged
    return attempt(tmp_path / "gateseal", root, outputs, shutil.which("git"))


def oids(root, outputs):
    return [blob(root, name) for name in ("gateseal.toml", "gateseal.lock", *outputs)]


class Became(Exception):
    """os.execve's call: the program, its arguments and its environment."""


def become(path, arguments, environment):
    raise Became(path, arguments, environment)


def test_agreeing_blobs_become_the_attempts_pre_commit(
    staged, published, monkeypatch, tmp_path
):
    root, outputs = staged
    record, paths = published
    python = paths["pre_commit_python"]
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "execve", become)
    with pytest.raises(Became) as became:
        main(["_staged", str(record), *oids(root, outputs)])
    program, arguments, environment = became.value.args
    assert program == str(python)
    assert arguments == [
        str(python),
        "-I",
        "-B",
        "-m",
        "pre_commit",
        "run",
        "--config",
        ".pre-commit-config.yaml",
        "--hook-stage",
        "pre-commit",
    ]
    assert Path.cwd() == root
    assert environment["PRE_COMMIT_HOME"] == str(paths["pre_commit_home"])


@pytest.mark.parametrize(
    "change",
    [
        lambda found: found[:-1],
        lambda found: [*found, found[-1]],
        lambda found: [*found[:-1], found[-1][:12]],
        lambda found: [found[0].upper(), *found[1:]],
    ],
    ids=["one missing", "one extra", "abbreviated", "uppercase"],
)
def test_unexpected_arguments_stop_the_commit(
    staged, published, capsys, monkeypatch, change
):
    root, outputs = staged
    record, _ = published
    monkeypatch.setattr(os, "execve", become)
    assert main(["_staged", str(record), *change(oids(root, outputs))]) == 3
    _, step = stderr_refusal(capsys.readouterr().err)
    assert step == bootstrap.ACTIVATE


def test_without_an_attempt_there_is_no_preparation(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "execve", become)
    assert main(["_staged"]) == 3
    _, step = stderr_refusal(capsys.readouterr().err)
    assert step == bootstrap.ACTIVATE


@pytest.mark.parametrize(
    "name,named,step",
    [
        (".pre-commit-config.yaml", ".pre-commit-config.yaml", bootstrap.CHECK),
        ("gateseal.lock", "Lock", "gateseal configure"),
    ],
    ids=["output", "lock"],
)
def test_an_edited_staged_file_names_its_recovery(
    staged, published, capsys, monkeypatch, name, named, step
):
    root, outputs = staged
    record, _ = published
    monkeypatch.setattr(os, "execve", become)
    found = oids(root, outputs)
    names = ["gateseal.toml", "gateseal.lock", *outputs]
    data = (root / name).read_bytes()
    if name == "gateseal.lock":
        document = json.loads(data)
        document["manifest_sha256"] = "0" * 64
        data = json.dumps(document).encode()
    else:
        data += b"# edited\n"
    found[names.index(name)] = stored(root, data)
    assert main(["_staged", str(record), *found]) == 2
    message, shown = stderr_refusal(capsys.readouterr().err)
    assert named in message
    assert shown == step


def test_a_missing_git_is_a_filesystem_failure(staged, capsys, monkeypatch, tmp_path):
    root, outputs = staged
    git = tmp_path / "missing" / "git"
    record, _ = attempt(tmp_path / "gateseal", root, outputs, git)
    monkeypatch.setattr(os, "execve", become)
    assert main(["_staged", str(record), *oids(root, outputs)]) == 4
    message, step = stderr_refusal(capsys.readouterr().err)
    assert str(git) in message
    assert step == bootstrap.ACTIVATE
