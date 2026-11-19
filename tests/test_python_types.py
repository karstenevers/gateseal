"""Pyright checks the types of a repository's uv project: python-types.

A consumer with a uv project selects the bundled policy's python-types
capability. Each commit starts the pinned Pyright with uv in the project. The
first commit installs attrs and Pyright, with its Node.js, from PyPI.
"""

import json
import shlex

import pytest
from conftest import BUNDLED_TOOLS, on_machine
from installation import clone, process, success
from uv_projects import (
    OWN_NODEENV,
    PRICING,
    TOTAL_WITH_TAX,
    WORKSPACE_MEMBER,
    WRONG_FIELD_TYPE,
    write_files,
    write_project,
)

pytestmark = pytest.mark.integration

PYRIGHT = BUNDLED_TOOLS["pyright"]
CAPABILITIES = ("hygiene", "python", "python-types")
ENTRY = (
    f"uv run --locked --all-packages --with 'pyright[nodejs]=={PYRIGHT.pin}' -- "
    "pyright --project .gateseal/pyright.toml"
)
NOTE = (
    f"Tool pyright {PYRIGHT.pin} for python-types: built-in, uv-project; the "
    "repository's uv.lock pins what it imports."
)


def configure(installed, root, code=0):
    flags = [option for name in CAPABILITIES for option in ("--capability", name)]
    return installed.cli(root, "configure", *flags, code=code)


@pytest.fixture(scope="module")
def module_typed(installed, tmp_path_factory):
    """A consumer whose uv project passes Pyright, after its first commit."""
    root = tmp_path_factory.mktemp("typed consumer ü").resolve()
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer space ü ")
        write_project(consumer)
        configure(installed, consumer)
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-m", "Adopt type checks"], consumer)
        success(["git", "tag", "adopted"], consumer)
    return consumer


@pytest.fixture
def typed(module_typed):
    """The adopted consumer, reset to its adoption after each test."""
    yield module_typed
    success(["git", "reset", "-q", "--hard", "adopted"], module_typed)
    success(["git", "clean", "-fdq"], module_typed)


def stage(root, name, text):
    (root / name).parent.mkdir(exist_ok=True)
    (root / name).write_text(text)
    success(["git", "add", "--", name], root)


def commit(root, *options):
    result = process(["git", "commit", "-m", "Change", *options], root)
    return result, result.stdout + result.stderr


def hooks(result, stage):
    """Return each hook's status in a run or gate result, by hook id."""
    entries = [d for d in result["diagnostics"] if d["category"] == "hook"]
    assert {entry["stage"] for entry in entries} == {stage}
    return {entry["hook"]: entry["status"] for entry in entries}


def test_the_typed_project_commits_and_status_names_the_kind(installed, typed):
    committed = success(["git", "ls-files"], typed).stdout.split("\n")
    assert ".gateseal/pyright.toml" in committed
    assert not [name for name in committed if name.startswith(".venv")]
    lock = json.loads((typed / "gateseal.lock").read_bytes())
    assert lock["components"]["pyright"] == {
        "package": "pyright[nodejs]",
        "version": PYRIGHT.pin,
        "hook_ids": ["pyright"],
    }
    config = json.loads((typed / ".pre-commit-config.yaml").read_bytes())
    every = [hook for repo in config["repos"] for hook in repo["hooks"]]
    (pyright,) = [hook for hook in every if hook["id"] == "pyright"]
    assert pyright["entry"] == ENTRY
    assert (pyright["always_run"], pyright["pass_filenames"]) == (True, False)
    status = installed.cli(typed, "status")
    assert status["code"] == "healthy"
    assert NOTE in [d["message"] for d in status["diagnostics"]]
    assert installed.cli(typed, "check")["code"] == "consistent"
    assert hooks(installed.cli(typed, "run"), "run")["pyright"] == "passed"
    assert hooks(installed.cli(typed, "gate"), "ci")["pyright"] == "passed"


def test_an_error_that_a_dependency_shows_stops_the_commit(typed):
    stage(typed, "checkout.py", WRONG_FIELD_TYPE)
    result, output = commit(typed)
    assert result.returncode == 1, output
    assert "pyright" in output and "Failed" in output
    assert "checkout.py:3:" in output
    assert (
        '"Literal[\'100\']" is not assignable to "int" (reportArgumentType)' in output
    )
    assert "could not be resolved" not in output
    heads = success(["git", "rev-parse", "HEAD", "adopted"], typed).stdout.split()
    assert heads[0] == heads[1]


def test_an_error_in_a_file_the_commit_leaves_alone_stops_it(typed):
    stage(typed, "src/shop/pricing.py", TOTAL_WITH_TAX)
    result, output = commit(typed)
    assert result.returncode == 1, output
    assert 'checkout.py:3:7 - error: Argument missing for parameter "tax"' in output
    assert (typed / "src/shop/pricing.py").read_text() != PRICING


def test_a_commit_that_only_deletes_a_module_stops_at_its_importer(typed):
    success(["git", "rm", "-q", "src/shop/pricing.py"], typed)
    result, output = commit(typed)
    assert result.returncode == 1, output
    assert 'checkout.py:1:6 - error: Import "shop.pricing" could not be resolved' in (
        output
    )


def test_pyright_reads_the_project_s_package_not_its_own_of_that_name(typed):
    write_files(typed, OWN_NODEENV)
    success(["git", "add", "-A"], typed)
    result, output = commit(typed)
    assert result.returncode == 0, output
    own = "import nodeenv; print(hasattr(nodeenv, 'marker'))"
    started = success(shlex.split(ENTRY)[:7] + ["python", "-c", own], typed)
    assert started.stdout == "False\n"


def test_pyright_reads_what_only_a_workspace_member_depends_on(typed):
    write_files(typed, WORKSPACE_MEMBER)
    success(["git", "add", "-A"], typed)
    result, output = commit(typed)
    assert result.returncode == 0, output


def test_pyright_reads_stubs_from_the_typings_folder_at_the_root(typed):
    stage(typed, "typings/legacy.pyi", "def rate() -> int: ...\n")
    stage(typed, "rates.py", "import legacy\n\nprint(legacy.rate())\n")
    result, output = commit(typed)
    assert result.returncode == 0, output


def test_run_and_the_gate_report_the_failed_hook(installed, typed):
    stage(typed, "checkout.py", WRONG_FIELD_TYPE)
    result, output = commit(typed, "--no-verify")
    assert result.returncode == 0, output
    ran = installed.cli(typed, "run", code=1)
    assert ran["code"] == "checks_failed"
    assert hooks(ran, "run")["pyright"] == "failed"
    base, head = success(["git", "rev-parse", "adopted", "HEAD"], typed).stdout.split()
    gated = installed.cli(typed, "gate", "--base", base, "--head", head, code=1)
    assert gated["code"] == "checks_failed"
    assert hooks(gated, "ci")["pyright"] == "failed"


def test_a_uv_lock_that_pyproject_outdates_stops_the_commit(typed):
    project = (typed / "pyproject.toml").read_text()
    stage(
        typed,
        "pyproject.toml",
        project.replace('["attrs==26.1.0"]', '["attrs==26.1.0", "idna==3.11"]'),
    )
    result, output = commit(typed)
    assert result.returncode == 1, output
    assert "`uv.lock` needs to be updated, but `--locked` was provided" in output


def test_a_repository_without_a_uv_project_is_refused(installed, consumer):
    refused = configure(installed, consumer, code=2)
    assert refused["code"] == "missing_project"
    assert refused["diagnostics"][0]["message"] == (
        "pyright runs in the repository's uv project, but the repository has no "
        "pyproject.toml. Only uv projects are supported."
    )
    assert not (consumer / "gateseal.toml").exists()
    write_project(consumer)
    configure(installed, consumer)
    (consumer / "uv.lock").unlink()
    for command in ("check", "run", "gate"):
        missing = installed.cli(consumer, command, code=2)
        assert missing["code"] == "missing_project", command
        assert "has no uv.lock" in missing["diagnostics"][0]["message"]
    status = installed.cli(consumer, "status")
    (failed,) = [d for d in status["diagnostics"] if d.get("status") == "failed"]
    assert failed["check"] == "lock" and "has no uv.lock" in failed["message"]
