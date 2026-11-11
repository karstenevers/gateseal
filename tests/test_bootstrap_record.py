import hashlib
import json
import platform
import re
import shutil
from pathlib import Path

import pytest
from gateseal import bootstrap
from prepared_attempts import executable as program

LOCK = b'{"schema":1,"compiler":"0.1.0"}\n'
REPLACED = "attempt-" + "f" * 32


def prepare(base, root, *, outputs=(), lock=LOCK, tools=True):
    """Write a complete attempt under base for root, as activation does.

    The record seals every store tree and names a hook environment, a native
    tool and a Hook Python when tools is true, each linked from the attempt.
    """
    stores = base / "stores"
    attempt = base / "gateseal" / ("attempt-" + "0123456789abcdef" * 2)
    paths = bootstrap.PreparedPaths(
        git=Path(shutil.which("git")),
        interpreter=program(stores / "interpreter/bin/python"),
        compiler_python=program(stores / "compiler/bin/python"),
        pre_commit_python=program(stores / "pre-commit/bin/python"),
        bootstrap=program(stores / "compiler/lib/bootstrap.py"),
        pre_commit_home=attempt / "pre-commit-home",
        tools=attempt / "tools",
    )
    (attempt / "hook").mkdir(parents=True)
    (attempt / "hook/python").symlink_to(paths.interpreter)
    (attempt / "hook/bootstrap.py").symlink_to(paths.bootstrap)
    paths.tools.mkdir()
    trees = [stores / "interpreter", stores / "compiler", stores / "pre-commit"]
    native, hook_python = {}, None
    if tools:
        native["ruff"] = bootstrap.PreparedTool("0.17.1", program(stores / "ruff/ruff"))
        (paths.tools / "gateseal-ruff").symlink_to(native["ruff"].executable)
        hook_python = bootstrap.PreparedTool(
            "3.15.2", program(stores / "hook python/bin/python3.15")
        )
        (paths.tools / "python3.15.2").symlink_to(hook_python.executable)
        trees += [stores / "ruff", stores / "hook python"]
    entry = stores / "hooks/black"
    environment = entry / "py_env-python3.15"
    marker = environment / ".install_state_v1"
    executable = program(environment / "bin/black")
    marker.write_text('{"additional_dependencies": []}')
    hook_environments = {
        environment: bootstrap.HookEnvironment(
            "black",
            program(environment / "bin/python"),
            hashlib.sha256(marker.read_bytes()).hexdigest(),
            (executable,),
        )
    }
    trees.append(entry)
    roots = tuple(trees)
    record = bootstrap.PreparedRecord(
        attempt=attempt,
        root=root,
        lock_sha256=hashlib.sha256(lock).hexdigest(),
        platform=platform.system() + "-" + platform.machine(),
        paths=paths,
        outputs=tuple(outputs),
        native_tools=native,
        hook_python=hook_python,
        trees={
            tree: bootstrap.TreeDigests(
                bootstrap.tree_digest(tree, roots, content=False),
                bootstrap.tree_digest(tree, roots, content=True),
            )
            for tree in roots
        },
        hook_environments=hook_environments,
        replaces=REPLACED if tools else None,
    )
    write(record.attempt, record.document())
    return record


def write(attempt, document):
    (attempt / bootstrap.RECORD).write_text(json.dumps(document))


@pytest.mark.parametrize("tools", [True, False], ids=["tools", "no tools"])
def test_a_commit_reads_back_the_record_activation_wrote(tmp_path, tools):
    record = prepare(
        tmp_path, tmp_path, outputs=(".pre-commit-config.yaml",), tools=tools
    )
    assert bootstrap.PreparedRecord.read(record.attempt) == record


def test_a_record_of_release_binaries_alone_reads_back(tmp_path):
    record = prepare(tmp_path, tmp_path)
    document = record.document()
    (environment,) = document["hook_environments"]
    document["hook_environments"] = {}
    document["trees"] = {
        tree: digests
        for tree, digests in document["trees"].items()
        if not Path(environment).is_relative_to(tree)
    }
    write(record.attempt, document)
    assert bootstrap.PreparedRecord.read(record.attempt).hook_environments == {}


def environment(document):
    (value,) = document["hook_environments"].values()
    return value


PROTOCOL = "protocol"
NATIVE = "native tool"
HOOK_PYTHON = "Hook Python"
HOOK_ENVIRONMENT = "hook environment"
SEALS = "seals"

EDITS = {
    "unknown field": (lambda d: d.update(unknown=1), PROTOCOL),
    "missing field": (lambda d: d.pop("replaces"), PROTOCOL),
    "boolean schema": (lambda d: d.update(schema=True), PROTOCOL),
    "schema 2": (lambda d: d.update(schema=2), PROTOCOL),
    "numeric replaces": (lambda d: d.update(replaces=5), PROTOCOL),
    "root not text": (lambda d: d.update(root=1), "JSON string"),
    "paths not an object": (lambda d: d.update(paths=[]), "JSON object"),
    "missing path": (
        lambda d: d["paths"].pop("tools"),
        "paths",
    ),
    "relative path": (
        lambda d: d["paths"].update(git="git"),
        "absolute",
    ),
    "outputs text": (
        lambda d: d.update(outputs=".pre-commit-config.yaml"),
        "outputs",
    ),
    "empty output": (
        lambda d: d["outputs"].append(""),
        "outputs",
    ),
    "native tool name": (
        lambda d: d.update(native_tools={"Ruff": d["native_tools"]["ruff"]}),
        NATIVE,
    ),
    "relative native tool": (
        lambda d: d["native_tools"]["ruff"].update(executable="ruff"),
        NATIVE,
    ),
    "native tool field": (
        lambda d: d["native_tools"]["ruff"].update(sha256="0" * 64),
        NATIVE,
    ),
    "hook python version": (
        lambda d: d["hook_python"].update(version="3.15"),
        HOOK_PYTHON,
    ),
    "relative hook python": (
        lambda d: d["hook_python"].update(executable="python3.15"),
        HOOK_PYTHON,
    ),
    "missing executables": (
        lambda d: environment(d).pop("executables"),
        HOOK_ENVIRONMENT,
    ),
    "relative executable": (
        lambda d: environment(d)["executables"].append("bin/black"),
        HOOK_ENVIRONMENT,
    ),
    "hook environment field": (
        lambda d: environment(d).pop("marker_sha256"),
        HOOK_ENVIRONMENT,
    ),
    "hook entry seal without its environment": (
        lambda d: d.update(hook_environments={}),
        SEALS,
    ),
    "hook environment outside every seal": (
        lambda d: d.update(hook_environments={"/elsewhere/env": environment(d)}),
        SEALS,
    ),
    "missing interpreter seal": (
        lambda d: d["trees"].pop(next(iter(d["trees"]))),
        SEALS,
    ),
    "extra seal": (
        lambda d: d["trees"].update({"/elsewhere": {"metadata": "", "content": ""}}),
        SEALS,
    ),
}


@pytest.mark.parametrize(("edit", "part"), EDITS.values(), ids=EDITS.keys())
def test_a_record_activation_could_not_have_written_is_refused(tmp_path, edit, part):
    record = prepare(tmp_path, tmp_path, outputs=(".pre-commit-config.yaml",))
    document = record.document()
    edit(document)
    write(record.attempt, document)
    with pytest.raises(bootstrap.BootstrapError, match=re.escape(part)) as raised:
        bootstrap.PreparedRecord.read(record.attempt)
    assert raised.value.next_step == bootstrap.ACTIVATE


INVALID = {
    "array": ("[]", ()),
    "duplicate key": ('{"schema": 1, "schema": 1}', ("{path}", "'schema'")),
    "truncated": ("{", ("{path}",)),
}


@pytest.mark.parametrize(("text", "named"), INVALID.values(), ids=INVALID.keys())
def test_a_record_that_is_not_one_json_object_is_refused(tmp_path, text, named):
    record = prepare(tmp_path, tmp_path)
    path = record.attempt / bootstrap.RECORD
    path.write_text(text)
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.PreparedRecord.read(record.attempt)
    assert all(token.format(path=path) in str(raised.value) for token in named)
    assert raised.value.next_step == bootstrap.ACTIVATE


def test_an_attempt_without_its_own_record_is_incomplete(tmp_path):
    record = prepare(tmp_path, tmp_path)
    copy = record.attempt.with_name("attempt-" + "1" * 32)
    copy.mkdir()
    incomplete = re.escape(str(copy))
    with pytest.raises(bootstrap.BootstrapError, match=incomplete):
        bootstrap.PreparedRecord.read(copy)
    (copy / bootstrap.RECORD).symlink_to(record.attempt / bootstrap.RECORD)
    with pytest.raises(bootstrap.BootstrapError, match=incomplete):
        bootstrap.PreparedRecord.read(copy)
