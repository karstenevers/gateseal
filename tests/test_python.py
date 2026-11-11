import hashlib
import json
import os
import shutil

import pytest
from conftest import preserved, published
from gateseal.policy import load_bundled_policy
from installation import clone, process, success

pytestmark = pytest.mark.integration

RUFF = "0.17.1"
POLICY = load_bundled_policy()
TRACKED = (
    "gateseal.toml",
    "gateseal.lock",
    ".pre-commit-config.yaml",
    ".gateseal/ruff.toml",
)
UNDEFINED = b"value = undefined_name\n"
CLEAN = b'"""A module Ruff accepts."""\n\nVALUE = 1\n'


def configure(installed, root, *capabilities, code=0):
    flags = [option for name in capabilities for option in ("--capability", name)]
    return installed.cli(
        root,
        "configure",
        *flags,
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
        code=code,
    )


def adopt(installed, root):
    configure(installed, root, "hygiene", "python")
    assert installed.cli(root, "activate")["code"] == "prepared"
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "-m", "Adopt Python checks"], root)
    success(["git", "tag", "adopted"], root)
    return root


@pytest.fixture(scope="module")
def module_checked(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("python consumer ü").resolve()
    return adopt(installed, clone(root / "origin", root / "consumer space ü "))


@pytest.fixture
def checked(module_checked):
    """The adopted consumer, reset to its adoption after each test."""
    yield module_checked
    success(["git", "reset", "-q", "--hard", "adopted"], module_checked)
    success(["git", "clean", "-fdq"], module_checked)


def stage(root, name, content):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    success(["git", "add", "--", name], root)
    return path


def commit(root, message="Change", env=None):
    result = process(["git", "commit", "-m", message], root, env)
    return result, result.stdout + result.stderr


def test_python_capability_locks_renders_and_prepares_ruff(installed, consumer):
    configure(installed, consumer, "hygiene", "python")
    for name in TRACKED:
        assert not (consumer / name).stat().st_mode & 0o111, name
        assert b"\r" not in (consumer / name).read_bytes()
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert list(lock["outputs"]) == [".gateseal/ruff.toml", ".pre-commit-config.yaml"]
    assert lock["components"]["ruff"] == {
        "repository": "https://github.com/astral-sh/ruff-pre-commit",
        "version": RUFF,
        "revision": "38bfb07c0c64c304f47eb52090f8288150c77730",
        "hook_ids": ["ruff-check", "ruff-format"],
        "tools": {"ruff": RUFF},
    }
    assert lock["components"]["pre-commit-hooks"]["tools"] == {}
    config = json.loads((consumer / ".pre-commit-config.yaml").read_bytes())
    ruff = config["repos"][1]
    assert [hook["id"] for hook in ruff["hooks"]] == ["ruff-check", "ruff-format"]
    assert ruff["hooks"][0]["args"][-1] == "--"
    assert installed.cli(consumer, "check")["code"] == "consistent"
    assert installed.cli(consumer, "activate")["code"] == "prepared"
    executables = {
        environment["component"]: [
            os.path.relpath(path, os.path.dirname(environment["python"]))
            for path in environment["executables"]
        ]
        for environment in json.loads(
            (published(consumer) / "prepared.json").read_bytes()
        )["hook_environments"].values()
    }
    assert executables == {
        "pre-commit-hooks": [
            "check-yaml",
            "end-of-file-fixer",
            "trailing-whitespace-fixer",
        ],
        "ruff": ["ruff"],
    }
    doctor = installed.cli(consumer, "doctor", "--verbose")
    assert doctor["code"] == "healthy"
    assert f"ruff {RUFF}" in json.dumps(doctor)
    assert installed.cli(consumer, "activate")["code"] == "already_prepared"


def test_hygiene_alone_renders_no_ruff(installed, configured):
    lock = json.loads((configured / "gateseal.lock").read_bytes())
    assert set(lock["components"]) == {"pre-commit-hooks"}
    assert not (configured / ".gateseal").exists()
    assert b"ruff" not in (configured / ".pre-commit-config.yaml").read_bytes()


def test_ruff_findings_stop_the_commit_with_file_line_and_rule(installed, checked):
    stage(checked, "app/bad.py", UNDEFINED)
    result, output = commit(checked)
    assert result.returncode == 1, output
    assert "ruff check" in output and "Failed" in output
    assert "app/bad.py:1:9: F821 Undefined name `undefined_name`" in output
    heads = success(["git", "rev-parse", "HEAD", "adopted"], checked).stdout.split()
    assert heads[0] == heads[1]


def test_safe_fixes_and_formatting_change_files_for_restaging(installed, checked):
    path = stage(checked, "messy.py", b"import os\nvalue=1\n")
    result, output = commit(checked)
    assert result.returncode == 1, output
    assert "files were modified by this hook" in output
    assert path.read_bytes() == b"value = 1\n"
    staged = success(["git", "show", ":messy.py"], checked).stdout
    assert staged == "import os\nvalue=1\n"
    success(["git", "add", "messy.py"], checked)
    result, output = commit(checked, "Fixed")
    assert result.returncode == 0, output


def test_unsafe_fixes_are_not_applied(installed, checked):
    # F841, an unused local variable, has only an unsafe fix.
    content = b"def f():\n    unused = 1\n"
    path = stage(checked, "unsafe.py", content)
    result, output = commit(checked)
    assert result.returncode == 1, output
    assert "unsafe.py:2:5: F841" in output
    assert path.read_bytes() == content


def test_legitimate_python_commits(installed, checked):
    stage(checked, "package/module.py", CLEAN)
    result, output = commit(checked, "Add module")
    assert result.returncode == 0, output
    assert "ruff check" in output and "ruff format" in output


@pytest.mark.parametrize(
    "staged,passes",
    [(CLEAN, True), (UNDEFINED, False), (b"value=1\n", False)],
    ids=["passes", "finding", "formatted"],
)
def test_unstaged_edits_survive_success_and_failure(installed, checked, staged, passes):
    unstaged = staged + b"\n\nEDITED = 2  # unstaged edit\n"
    path = stage(checked, "partial.py", staged)
    path.write_bytes(unstaged)
    result, output = commit(checked, "Partial")
    assert (result.returncode == 0) is passes, output
    assert b"EDITED = 2  # unstaged edit" in path.read_bytes()
    if passes:
        committed = success(["git", "show", "HEAD:partial.py"], checked).stdout
        assert committed.encode() == staged
        assert path.read_bytes() == unstaged
    else:
        assert success(["git", "show", ":partial.py"], checked).stdout.encode() == (
            staged
        )


def test_a_file_named_like_an_option_is_checked(installed, checked):
    stage(checked, "--exit-zero.py", UNDEFINED)
    result, output = commit(checked)
    assert result.returncode == 1, output
    assert "--exit-zero.py:1:9: F821" in output


def test_repository_ruff_settings_and_variables_do_not_change_the_policy(
    installed, checked
):
    stage(checked, "ruff.toml", b'[lint]\nignore = ["F"]\n')
    stage(checked, "pyproject.toml", b'[tool.ruff.lint]\nignore = ["F"]\n')
    stage(checked, "nested/ruff.toml", b'[lint]\nignore = ["F"]\n')
    stage(checked, "nested/bad.py", UNDEFINED)
    hidden = checked.parent / "ruff findings.json"
    env = dict(os.environ, RUFF_OUTPUT_FORMAT="json", RUFF_OUTPUT_FILE=str(hidden))
    result, output = commit(checked, env=env)
    assert result.returncode == 1, output
    assert "nested/bad.py:1:9: F821" in output
    assert not hidden.exists()
    assert (checked / "ruff.toml").read_bytes() == b'[lint]\nignore = ["F"]\n'


def test_run_reports_findings_as_structured_diagnostics(installed, checked):
    passed = installed.cli(checked, "run")
    assert passed["code"] == "checks_passed"
    hooks = {d["hook"]: d for d in passed["diagnostics"] if d["category"] == "hook"}
    for hook in ("ruff-check", "ruff-format"):
        assert hooks[hook]["status"] == "skipped", hooks
        assert hooks[hook]["message"].endswith("(no files to check)")
        assert hooks[hook]["tool"] == "ruff" and hooks[hook]["stage"] == "run"
    stage(checked, "src/bad.py", b"import os\n" + UNDEFINED)
    failed = installed.cli(checked, "run", code=1)
    assert failed["code"] == "checks_failed"
    findings = [d for d in failed["diagnostics"] if d["category"] == "finding"]
    assert findings == [
        {
            "severity": "error",
            "category": "finding",
            "tool": "ruff",
            "origin": "built-in",
            "hook": "ruff-check",
            "stage": "run",
            "file": "src/bad.py",
            "line": 1,
            "column": 9,
            "rule": "F821",
            "message": "Undefined name `undefined_name`",
        }
    ]
    hooks = {d["hook"]: d for d in failed["diagnostics"] if d["category"] == "hook"}
    assert hooks["ruff-check"]["status"] == "failed"
    assert hooks["ruff-check"]["modified"] is True
    assert failed["next_steps"] == [
        "Fix each finding at its file and line.",
        "Review the files that hooks changed with git diff.",
        "Stage the fixes, then run gateseal run again.",
    ]
    human = process(
        [installed.command, "--repo", checked, "run"], checked, installed.environment
    )
    assert human.returncode == 1
    assert "src/bad.py:1:9: F821 Undefined name `undefined_name`" in human.stdout


def test_a_damaged_ruff_stops_commits_before_a_host_ruff_runs(
    installed, consumer, tmp_path
):
    # Hooks search the directory of the Git that activation found, so a Ruff
    # installed beside it would stand in for a damaged prepared Ruff.
    host = tmp_path / "host bin"
    host.mkdir()
    (host / "git").symlink_to(shutil.which("git"))
    ran = tmp_path / "host-ruff-ran"
    (host / "ruff").write_text(f"#!/bin/sh\ntouch '{ran}'\n")
    (host / "ruff").chmod(0o755)
    path = f"{host}{os.pathsep}{installed.environment['PATH']}"
    store = str(tmp_path / "store")
    env = dict(installed.environment, PATH=path, GATESEAL_INTERPRETER_STORE=store)
    configure(installed, consumer, "hygiene", "python")
    assert installed.cli(consumer, "activate", env=env)["code"] == "prepared"
    record = json.loads((published(consumer) / "prepared.json").read_bytes())
    assert record["paths"]["git"] == str(host / "git")
    python = next(
        environment["python"]
        for environment in record["hook_environments"].values()
        if environment["component"] == "ruff"
    )
    ruff = os.path.join(os.path.dirname(python), "ruff")
    success(["git", "add", *TRACKED], consumer)
    stage(consumer, "bad.py", UNDEFINED)
    os.chmod(ruff, 0o644)
    result, output = commit(consumer, env=env)
    assert result.returncode != 0
    assert f"Hook executable {ruff} is missing or not executable." in output
    assert "gateseal activate" in output
    assert not ran.exists()
    damaged = installed.cli(consumer, "doctor", code=3)
    assert damaged["code"] == "needs_activation"
    assert ruff in damaged["diagnostics"][0]["message"]
    assert installed.cli(consumer, "activate", env=env)["code"] == "prepared"
    assert installed.cli(consumer, "doctor")["code"] == "healthy"
    result, output = commit(consumer, env=env)
    assert result.returncode == 1
    assert "bad.py:1:9: F821" in output
    assert not ran.exists()


def test_configure_adds_python_through_the_manifest(installed, configured):
    refused = configure(installed, configured, "hygiene", "python", code=2)
    assert refused["code"] == "configure_assertion_failed"
    assert not (configured / ".gateseal").exists()
    manifest = configured / "gateseal.toml"
    manifest.write_text(
        manifest.read_text().replace('["hygiene"]', '["python", "hygiene"]')
    )
    assert installed.cli(configured, "configure")["changed"] == [
        "gateseal.lock",
        ".gateseal/ruff.toml",
        ".pre-commit-config.yaml",
    ]
    assert installed.cli(configured, "configure")["changed"] == []
    owned = configured / ".gateseal/ruff.toml"
    with preserved(owned) as original:
        owned.write_bytes(original + b'extend-exclude = ["legacy"]\n')
        for command in ("check", "configure"):
            edited = installed.cli(configured, command, code=2)
            assert edited["code"] == "ownership_conflict"
            assert ".gateseal/ruff.toml" in json.dumps(edited)


def test_configure_refuses_an_unmanaged_ruff_configuration(installed, consumer):
    (consumer / ".gateseal").mkdir()
    unmanaged = consumer / ".gateseal/ruff.toml"
    unmanaged.write_bytes(b"line-length = 120\n")
    refused = configure(installed, consumer, "hygiene", "python", code=2)
    assert refused["code"] == "ownership_conflict"
    assert unmanaged.read_bytes() == b"line-length = 120\n"
    assert not (consumer / "gateseal.lock").exists()


NEWER = b"""import os
import tomllib

import yaml


def describe(value):
    match value:
        case 1:
            return t"one {value}"
    try:
        return os, tomllib, yaml
    except ValueError, TypeError:
        return None
"""
"""Python 3.14 that Ruff accepts and formats, with three syntax errors and an
unsorted import block on Python 3.9, whose standard library lacks tomllib."""


def choose_target(installed, root, manifest, value, code=0):
    (root / "gateseal.toml").write_bytes(
        manifest + f"\n[choices.python]\ntarget_version = {value}\n".encode()
    )
    return installed.cli(root, "configure", code=code)


def owned(root):
    return {name: (root / name).read_bytes() for name in TRACKED[1:]}


def prepared_ruff(root):
    environments = json.loads((published(root) / "prepared.json").read_bytes())
    python = next(
        environment["python"]
        for environment in environments["hook_environments"].values()
        if environment["component"] == "ruff"
    )
    return os.path.join(os.path.dirname(python), "ruff")


def test_a_repository_on_an_older_python_gets_findings_for_newer_syntax(
    installed, consumer
):
    (rules,) = (a.content for a in POLICY.assets if a.path == "rules/ruff.toml")
    configure(installed, consumer, "hygiene", "python")
    assert (consumer / ".gateseal/ruff.toml").read_bytes() == (
        b'target-version = "py311"\n' + rules
    )
    manifest = (consumer / "gateseal.toml").read_bytes()
    default = json.loads((consumer / "gateseal.lock").read_bytes())
    before = owned(consumer)
    refused = choose_target(installed, consumer, manifest, '"py316"', code=2)
    assert refused["code"] == "invalid_choice"
    assert "py315, not 'py316'" in refused["diagnostics"][0]["message"]
    assert owned(consumer) == before

    chosen = choose_target(installed, consumer, manifest, '"py39"')
    assert sorted(chosen["changed"]) == [".gateseal/ruff.toml", "gateseal.lock"]
    ruff_toml = (consumer / ".gateseal/ruff.toml").read_bytes()
    assert ruff_toml == b'target-version = "py39"\n' + rules
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["choices"] == {
        "hygiene": {"exclude": []},
        "python": {"target_version": "py39"},
    }
    assert lock["manifest_sha256"] != default["manifest_sha256"]
    output = lock["outputs"][".gateseal/ruff.toml"]["sha256"]
    assert output == hashlib.sha256(ruff_toml).hexdigest()
    assert output != default["outputs"][".gateseal/ruff.toml"]["sha256"]
    status = installed.cli(consumer, "status")
    assert [
        d["message"] for d in status["diagnostics"] if d["category"] == "policy_choice"
    ] == ["Choice hygiene.exclude: nothing.", "Choice python.target_version: py39."]

    assert installed.cli(consumer, "activate")["code"] == "prepared"
    success(["git", "add", *TRACKED], consumer)
    success(["git", "commit", "-m", "Target Python 3.9"], consumer)
    path = stage(consumer, "newer.py", NEWER)
    result, output = commit(consumer)
    assert result.returncode == 1, output
    for finding in (
        "newer.py:8:5: invalid-syntax: Cannot use `match` statement on Python 3.9",
        "newer.py:10:20: invalid-syntax: Cannot use t-strings on Python 3.9",
        "newer.py:13:12: invalid-syntax: Multiple exception types must be "
        "parenthesized on Python 3.9",
        "Found 4 errors (1 fixed, 3 remaining).",
    ):
        assert finding in output
    assert path.read_bytes().startswith(b"import os\n\nimport tomllib\nimport yaml\n")
    assert success(["git", "log", "--format=%s"], consumer).stdout == (
        "Target Python 3.9\n"
    )
    path.write_bytes(NEWER)
    native = process(
        [prepared_ruff(consumer), "check", "--config", ".gateseal/ruff.toml"]
        + ["--no-cache", "--output-format", "concise", "newer.py"],
        consumer,
    )
    assert native.returncode == 1, native.stdout + native.stderr
    assert [line.split(" ", 2)[:2] for line in native.stdout.splitlines()[:4]] == [
        ["newer.py:1:1:", "I001"],
        ["newer.py:8:5:", "invalid-syntax:"],
        ["newer.py:10:20:", "invalid-syntax:"],
        ["newer.py:13:12:", "invalid-syntax:"],
    ]

    choose_target(installed, consumer, manifest, '"py314"')
    assert installed.cli(consumer, "activate")["code"] == "prepared"
    success(["git", "add", *TRACKED, "newer.py"], consumer)
    result, output = commit(consumer, "Target Python 3.14")
    assert result.returncode == 0, output
    assert success(["git", "show", "HEAD:newer.py"], consumer).stdout == (
        NEWER.decode()
    )


NOTEBOOK = json.dumps(
    {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["VALUE = 1\n"],
            },
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["print(VALUE)\n", "print(undefined_name)\n"],
            },
        ],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    },
    indent=1,
).encode()


def test_run_reads_colored_output_and_notebook_cells(installed, checked):
    stage(checked, "notebooks/analysis.ipynb", NOTEBOOK + b"\n")
    env = dict(installed.environment, PRE_COMMIT_COLOR="always", FORCE_COLOR="1")
    failed = installed.cli(checked, "run", code=1, env=env)
    findings = [d for d in failed["diagnostics"] if d["category"] == "finding"]
    assert [
        (f["file"], f.get("cell"), f["line"], f["column"], f["rule"]) for f in findings
    ] == [("notebooks/analysis.ipynb", 2, 2, 7, "F821")]
    hooks = {d["hook"]: d for d in failed["diagnostics"] if d["category"] == "hook"}
    assert hooks["ruff-check"]["status"] == "failed"
