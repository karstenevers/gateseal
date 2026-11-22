"""The node-project examples that the docs publish are policies that tests lock.

docs/add-a-tool.md's tsc-app definition and table, and docs/typescript.md's
typescript-types table and its version exception, are the files these tests
write into a team policy. A fake upstream answers where gateseal policy lock
would ask nodejs.org and the npm registry.
"""

import json
import shlex
import tomllib

from doc_examples import example
from gateseal import authoring
from gateseal.policy import bundled_root, load_policy_tree
from gateseal.tools.node import Package
from node_policy import VERSION, released
from node_project_policy import (
    HEADER,
    TYPESCRIPT,
    install_arguments,
    registry,  # noqa: F401
)
from test_npm_package import GENERATED
from test_team_tools import consumer, team

APP_DEFINITION = example("add-a-tool.md", "policy file: tools/tsc-app.toml")
APP_TABLE = example("add-a-tool.md", "policy tables: tsc-app")
TYPES_TABLE = example("typescript.md", "policy tables: typescript-types")
EXCEPTION = example("typescript.md", "manifest tables: tsc exception")


def policy_root(tmp_path, table, files=None):
    """Write a team policy of HEADER and table, with only the files it names."""
    files = {
        "rules/gitleaks.toml": None,
        "tools/yamllint.toml": None,
        "tools/gitleaks.toml": None,
    } | (files or {})
    return team(tmp_path, f"{HEADER}\n{table}", files)


def app_policy(tmp_path):
    return policy_root(tmp_path, APP_TABLE, {"tools/tsc-app.toml": APP_DEFINITION})


def own_hooks(locked):
    config = json.loads(locked.snapshot.outputs[".pre-commit-config.yaml"])
    return config["repos"][0]["hooks"], {
        hook["id"]: hook for repo in config["repos"][1:] for hook in repo["hooks"]
    }


def test_the_tsc_app_example_locks_its_typescript_package(tmp_path, registry):  # noqa: F811
    root = app_policy(tmp_path)
    assert authoring.lock(root).code == "locked"
    assert authoring.check(root).code == "consistent"
    components = json.loads((root / "policy.lock").read_bytes())["components"]
    assert components["tsc-app"] == {
        "versions": {TYPESCRIPT: {"package": "typescript", "packages": {}}}
    }
    assert released("typescript", TYPESCRIPT) in registry.downloaded


def test_the_tsc_app_example_runs_at_a_push_after_the_install(tmp_path, registry):  # noqa: F811
    root = app_policy(tmp_path)
    authoring.lock(root)
    policy = load_policy_tree(root)
    locked, compiled = consumer(policy, ("app-types",))
    node = policy.tools["tsc-app"].node
    own, hooks = own_hooks(locked)
    assert own[-1] == {
        "id": "gateseal-node-packages",
        "args": install_arguments(node),
        "stages": ["pre-push", "manual"],
    }
    launch = shlex.join(node.launch([Package("typescript", TYPESCRIPT)], "tsc"))
    assert hooks["tsc-app"] == {
        "id": "tsc-app",
        "name": "Check the types of the app with tsc",
        "stages": ["pre-push", "manual"],
        "always_run": True,
        "pass_filenames": False,
        "exclude": GENERATED,
        "language": "unsupported",
        "entry": f"{launch} --noEmit --project tsconfig.app.json --strict",
        "require_serial": True,
    }
    assert compiled["native_artifacts"]["node"]["version"] == VERSION


def test_the_tsc_exception_selects_a_version_that_the_table_reviews(
    tmp_path,
    registry,  # noqa: F811
):
    shown = tomllib.loads(TYPES_TABLE)["capabilities"]["typescript-types"]
    for version in shown["tools"]["tsc"]["reviewed"]:
        registry.publish("typescript", version)
    root = policy_root(tmp_path, TYPES_TABLE)
    authoring.lock(root)
    policy = load_policy_tree(root)
    _, compiled = consumer(policy, ("typescript-types",), f"\n{EXCEPTION}")
    wanted = tomllib.loads(EXCEPTION)["exceptions"]["tsc"]["version"]
    assert wanted != shown["tools"]["tsc"]["pin"]
    assert compiled["components"]["tsc"]["version"] == wanted


def test_the_typescript_types_example_is_part_of_the_bundled_policy():
    shown = tomllib.loads(TYPES_TABLE)
    bundled = tomllib.loads((bundled_root() / "policy.toml").read_text())
    capability = bundled["capabilities"]["typescript-types"]
    assert shown["capabilities"]["typescript-types"] == capability
