"""Pyright's hook installs exactly what uv.lock pins, in its own environment.

A consumer with a uv project selects the bundled policy's python-types
capability. The hook's environment, .gateseal/environments/pyright, holds
what a new clone installs: no package that uv.lock dropped, and no extra that
a developer installed in .venv. A package of the project's own that shares
Pyright's name and pin stops the commit before Pyright's hook starts, and the gate.
"""

import shutil

import pytest
from conftest import BUNDLED_TOOLS
from installation import success
from test_python_types import commit, hooks, module_typed, typed  # noqa: F401
from uv_projects import PYPROJECT, write_files

pytestmark = pytest.mark.integration

PIN = BUNDLED_TOOLS["pyright"].pin
LABELS = 'import idna\n\nprint(idna.encode("example.com"))\n'
WITH_IDNA = PYPROJECT.replace('["attrs==26.1.0"]', '["attrs==26.1.0", "idna==3.11"]')
MISSING_IDNA = 'labels.py:1:8 - error: Import "idna" could not be resolved'
OWN_PYRIGHT = {
    "pyproject.toml": PYPROJECT
    + f'\n[dependency-groups]\ndev = ["pyright=={PIN}"]\n'
    + '\n[tool.uv.sources]\npyright = { path = "vendor/pyright" }\n',
    "vendor/pyright/pyproject.toml": f"""\
[project]
name = "pyright"
version = "{PIN}"
requires-python = ">=3.12"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""",
    "vendor/pyright/src/pyright/__init__.py": "",
}
"""The project with a package of its own named pyright, at Pyright's pin."""
SHADOWED = (
    f"uv.lock installs pyright {PIN} from its directory source 'vendor/pyright', so "
    "the project's own package would run in place of the reviewed pyright."
)


def test_the_hook_s_environment_is_its_own_and_git_ignores_it(typed):  # noqa: F811
    assert (typed / ".gateseal/environments/pyright/bin/python").exists()
    assert not (typed / ".venv").exists()
    assert success(["git", "status", "--porcelain"], typed).stdout == ""


def test_a_package_that_uv_lock_dropped_stops_the_commit_as_the_gate_does(
    installed,
    typed,  # noqa: F811
    tmp_path,
):
    write_files(typed, {"pyproject.toml": WITH_IDNA, "labels.py": LABELS})
    success(["git", "add", "-A"], typed)
    result, output = commit(typed)
    assert result.returncode == 0, output
    write_files(typed, {"pyproject.toml": PYPROJECT})
    success(["git", "add", "-A"], typed)
    result, output = commit(typed)
    assert result.returncode == 1, output
    assert MISSING_IDNA in output
    result, output = commit(typed, "--no-verify")
    assert result.returncode == 0, output
    clone = tmp_path / "new clone"
    success(["git", "clone", "--quiet", typed, clone], tmp_path)
    base, head = success(["git", "rev-parse", "HEAD~", "HEAD"], clone).stdout.split()
    gated = installed.cli(clone, "gate", "--base", base, "--head", head, code=1)
    assert hooks(gated, "ci")["pyright"] == "failed"
    assert [d for d in gated["diagnostics"] if MISSING_IDNA in d["message"]]


def test_an_extra_that_a_developer_installed_stays_out_of_the_hook(typed):  # noqa: F811
    extra = PYPROJECT + '\n[project.optional-dependencies]\nnet = ["idna==3.11"]\n'
    write_files(typed, {"pyproject.toml": extra, "labels.py": LABELS})
    success(["uv", "sync", "--quiet", "--extra", "net"], typed)
    success(["git", "add", "-A"], typed)
    try:
        result, output = commit(typed)
        assert result.returncode == 1, output
        assert MISSING_IDNA in output
        site = typed / ".venv/lib"
        assert list(site.glob("python*/site-packages/idna/__init__.py"))
    finally:
        shutil.rmtree(typed / ".venv")


def test_a_package_of_the_project_named_like_pyright_stops_the_commit(
    installed,
    typed,  # noqa: F811
):
    write_files(typed, OWN_PYRIGHT)
    success(["git", "add", "-A"], typed)
    result, output = commit(typed)
    assert result.returncode == 1, output
    assert f"gateseal: {SHADOWED}" in output
    assert not [line for line in output.splitlines() if line.startswith("pyright")]
    gated = installed.cli(typed, "gate", code=2)
    assert gated["code"] == "shadowed_tool"
    assert gated["diagnostics"][0]["message"] == SHADOWED


def test_pyright_from_pypi_in_the_project_passes(typed):  # noqa: F811
    pypi = PYPROJECT + f'\n[dependency-groups]\ndev = ["pyright=={PIN}"]\n'
    write_files(typed, {"pyproject.toml": pypi})
    success(["git", "add", "-A"], typed)
    result, output = commit(typed)
    assert result.returncode == 0, output
