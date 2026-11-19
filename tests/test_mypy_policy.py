"""A team runs mypy in the uv project, as docs/add-a-tool.md shows.

A release of a team policy runs the how-to's mypy in place of Pyright. A
consumer with a uv project adopts it and commits. The first commit installs
attrs and mypy from PyPI. The policy also holds a tool of the ci stage whose
program prints the range that pre-commit names in its environment.
"""

import json

import pytest
from conftest import on_machine
from doc_examples import DOCS
from gateseal.validation import canonical
from installation import clone, process, success
from mypy_policy import VERSION, add_mypy
from team_policy import URL, environment, git, publish
from uv_projects import TOTAL_WITH_TAX, WRONG_FIELD_TYPE, write_project

pytestmark = pytest.mark.integration

PRINT_RANGE = (
    "import os, sys; "
    "sys.exit('range %(PRE_COMMIT_FROM_REF)s %(PRE_COMMIT_TO_REF)s' % os.environ)"
)
RANGE_TOOL = f"""\
kind = "uv-project"
stages = ["ci"]
scope = "project"

[source]
package = "mypy"

[executable]
name = "python"

[hooks.range]
name = "range"
args = ["-c", {json.dumps(PRINT_RANGE)}]
output = "plain"
"""
RANGE_TABLE = f"""\
[capabilities.python-types.tools.range]
pin = "{VERSION}"
reviewed = ["{VERSION}"]

"""


def add_tools(data):
    """Offer the how-to's mypy, and the tool that prints the gate's range."""
    add_mypy(data)
    (data / "tools/range.toml").write_text(RANGE_TOOL)
    policy = data / "policy.toml"
    text = policy.read_text()
    at = text.index("[capabilities.secrets.")
    policy.write_text(text[:at] + RANGE_TABLE + text[at:])
    lock = json.loads((data / "policy.lock").read_bytes())
    lock["components"]["range"] = lock["components"]["mypy"]
    (data / "policy.lock").write_bytes(canonical(lock))


def mypy_in_python(data):
    """Move the how-to's mypy to the python capability, beside Ruff."""
    add_tools(data)
    policy = data / "policy.toml"
    policy.write_text(
        policy.read_text().replace(
            "[capabilities.python-types.tools.mypy]", "[capabilities.python.tools.mypy]"
        )
    )


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    """The team policy's repository, with the how-to's release as v1."""
    origin = tmp_path_factory.mktemp("mypy policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", add_tools)
    return origin


def configure(installed, consumer, env, *capabilities):
    flags = [option for name in capabilities for option in ("--capability", name)]
    source = ("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1")
    return installed.cli(consumer, "configure", *flags, *source, env=env)


@pytest.fixture(scope="module")
def module_typed(installed, origin, tmp_path_factory):
    """A consumer of the how-to's policy whose uv project passes mypy."""
    root = tmp_path_factory.mktemp("mypy consumer ü")
    env = environment(installed, origin)
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        write_project(consumer, "shop")
        configure(installed, consumer, env, "hygiene", "python-types")
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-m", "Check types with mypy"], consumer, env)
        success(["git", "tag", "adopted"], consumer)
    return consumer, env


@pytest.fixture
def typed(module_typed):
    """The adopted consumer and its environment, reset after each test."""
    yield module_typed
    consumer, _ = module_typed
    success(["git", "reset", "-q", "--hard", "adopted"], consumer)
    success(["git", "clean", "-fdq"], consumer)


def commit(consumer, env, name, text, *options):
    (consumer / name).write_text(text)
    success(["git", "add", name], consumer)
    result = process(["git", "commit", "-m", f"Change {name}", *options], consumer, env)
    return result, result.stdout + result.stderr


def test_the_generated_hook_is_the_command_the_how_to_shows(typed):
    consumer, _ = typed
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["components"]["mypy"] == {
        "package": "mypy",
        "version": VERSION,
        "hook_ids": ["mypy"],
    }
    config = json.loads((consumer / ".pre-commit-config.yaml").read_bytes())
    every = [hook for repo in config["repos"] for hook in repo["hooks"]]
    (mypy,) = [hook for hook in every if hook["id"] == "mypy"]
    assert f"```sh\n{mypy['entry']}\n```" in (DOCS / "add-a-tool.md").read_text()


def test_mypy_reads_the_packages_that_the_project_installs(typed):
    consumer, env = typed
    result, output = commit(consumer, env, "checkout.py", WRONG_FIELD_TYPE)
    assert result.returncode == 1, output
    assert "- hook id: mypy" in output
    assert (
        'checkout.py:3: error: Argument 1 to "Price" has incompatible type "str"; '
        'expected "int"  [arg-type]'
    ) in output


def test_mypy_checks_a_file_the_commit_leaves_alone(typed):
    consumer, env = typed
    result, output = commit(consumer, env, "shop/pricing.py", TOTAL_WITH_TAX)
    assert result.returncode == 1, output
    assert 'checkout.py:3: error: Missing positional argument "tax"' in output


def test_a_program_reads_the_range_that_the_gate_checks(installed, typed):
    consumer, env = typed
    result, output = commit(consumer, env, "notes.py", "NOTE = 1\n")
    assert result.returncode == 0, output
    base, head = success(
        ["git", "rev-parse", "adopted", "HEAD"], consumer
    ).stdout.split()
    gated = installed.cli(
        consumer, "gate", "--base", base, "--head", head, code=1, env=env
    )
    hooks = {
        d["hook"]: d["status"] for d in gated["diagnostics"] if d["category"] == "hook"
    }
    assert (hooks["mypy"], hooks["range"]) == ("passed", "failed")
    assert f"range {base} {head}\n" in gated["diagnostics"][1]["message"]


def test_an_upgrade_needs_the_project_only_for_a_project_tool_of_its_target(
    installed, origin, tmp_path
):
    """A release moves mypy into a capability that a plain repository selects.

    The upgrade to it needs the uv project before it writes. The upgrade back
    needs none, also once uv.lock is gone.
    """
    publish(origin, "v2", mypy_in_python)
    env = environment(installed, origin)
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    configure(installed, consumer, env, "hygiene", "python")
    names = ("gateseal.toml", "gateseal.lock", ".pre-commit-config.yaml")
    before = [(consumer / name).read_bytes() for name in names]
    refused = installed.cli(consumer, "upgrade", "--policy-tag", "v2", code=2, env=env)
    assert refused["code"] == "missing_project"
    assert refused["diagnostics"][0]["message"] == (
        "mypy runs in the repository's uv project, but the repository has no "
        "pyproject.toml. Only uv projects are supported."
    )
    assert [(consumer / name).read_bytes() for name in names] == before
    write_project(consumer, "shop")
    upgrade = ("upgrade", "--policy-tag")
    assert installed.cli(consumer, *upgrade, "v2", env=env)["code"] == "upgraded"
    (consumer / "uv.lock").unlink()
    assert installed.cli(consumer, "check", code=2, env=env)["code"] == (
        "missing_project"
    )
    assert installed.cli(consumer, *upgrade, "v1", env=env)["code"] == "upgraded"


@pytest.mark.network
def test_policy_lock_asks_pypi_for_each_release(installed, tmp_path):
    """Lock the bundled Pyright and the how-to's mypy from PyPI."""
    policy = tmp_path / "policy"
    installed.cli(tmp_path, "policy", "init", str(policy))
    bundled = (policy / "policy.lock").read_bytes()
    lock = json.loads(bundled)
    del lock["components"]["pyright"]
    (policy / "policy.lock").write_bytes(canonical(lock))
    installed.cli(tmp_path, "policy", "lock", str(policy))
    assert (policy / "policy.lock").read_bytes() == bundled
    add_mypy(policy, locked=False)
    installed.cli(tmp_path, "policy", "lock", str(policy))
    lock = json.loads((policy / "policy.lock").read_bytes())
    assert lock["components"]["mypy"] == {"versions": {VERSION: {"package": "mypy"}}}
    missing = installed.cli(
        tmp_path, "policy", "add", "mypy", "0.0.0", str(policy), code=2
    )
    assert missing["code"] == "upstream_release_missing"
