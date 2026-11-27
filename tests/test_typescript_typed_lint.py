"""ESLint's rules that need type information run at a push and in the gate.

A consumer with an npm or a pnpm project selects the bundled policy's
typescript and typescript-typed-lint capabilities. The commit-time ESLint
runs without type information. The Built-in Tool eslint-typed runs at each
push, after gateseal's own hook has installed the project's packages, and
reads the project's tsconfig.json. Each push is a real git push to a bare
repository through the launcher that pre-commit wrote.
"""

import json
from pathlib import Path

import pytest
from conftest import BUNDLED_TOOLS, on_machine
from installation import success
from node_projects import MANAGERS, NEWER, NPM, PINNED, write_package, write_project
from test_typescript_types import INSTALL, Project, status

pytestmark = pytest.mark.integration

ESLINT_TYPED = BUNDLED_TOOLS["eslint-typed"]
ADOPT = ("--capability", "typescript", "--capability", "typescript-typed-lint")
TYPED = "eslint with types"
RULE = "@typescript-eslint/no-floating-promises"
NOTE = (
    f"Tool eslint-typed {ESLINT_TYPED.pin} for typescript-typed-lint: built-in, "
    f"node-project on Node {ESLINT_TYPED.node.version}; the repository's lockfile "
    "pins what it imports."
)
FLOATING = """\
import { Item } from "./shop.js";

export const check = (value: unknown): void => {
  Item.parseAsync(value);
};
"""
"""A promise that no code awaits, which only zod's types show."""
AWAITED = FLOATING.replace("  Item.parseAsync", "  void Item.parseAsync")
DISABLED = FLOATING.replace(
    "  Item.parseAsync", f"  // eslint-disable-next-line {RULE}\n  Item.parseAsync"
)
THROWING = 'throw new Error("the repository\'s ESLint configuration ran");\n'


@pytest.fixture(scope="module", params=MANAGERS, ids=lambda manager: manager.name)
def module_project(request, installed, tmp_path_factory):
    """A project of each package manager, adopted and pushed without its hooks."""
    manager = request.param
    root = tmp_path_factory.mktemp(f"{manager.name} typed consumer ü").resolve()
    remote = root / "remote.git"
    project = Project(root / "clone", remote, manager, installed.environment)
    with on_machine(installed):
        success(["git", "init", "--quiet", "--bare", "-b", "main", remote], root)
        success(["git", "clone", "--quiet", remote, project.clone], root)
        project.git("config", "user.name", "gateseal test")
        project.git("config", "user.email", "gateseal@example.invalid")
        write_project(project.clone, manager)
        installed.cli(project.clone, "configure", *ADOPT, env=project.env)
        installed.cli(project.clone, "setup", "--refresh", env=project.env)
        project.commit()
        project.git("tag", "adopted")
        project.git("push", "--quiet", "--no-verify", "origin", "main")
    return project


@pytest.fixture
def project(module_project):
    """The adopted project without node_modules, reset with its remote after a test."""
    yield module_project
    adopted = module_project.git("rev-parse", "adopted")
    success(["git", "update-ref", "refs/heads/main", adopted], module_project.remote)
    module_project.git("update-ref", "refs/remotes/origin/main", adopted)
    module_project.git("reset", "--quiet", "--hard", adopted)
    module_project.git("clean", "-fdxq")


def test_a_floating_promise_commits_and_stops_the_push_and_the_gate(installed, project):
    committed = project.commit({"src/check.ts": FLOATING})
    assert status("eslint", committed) == "Passed", committed
    assert status(TYPED, committed) is None
    code, shown = project.push()
    assert code != 0, shown
    assert (status(INSTALL, shown), status(TYPED, shown)) == ("Passed", "Failed")
    assert "src/check.ts" in shown
    assert RULE in shown
    assert project.pushed() == project.git("rev-parse", "adopted")
    gated = installed.cli(project.clone, "gate", code=1, env=project.env)
    assert gated["code"] == "checks_failed"
    failed = [
        (d["hook"], d["node"])
        for d in gated["diagnostics"]
        if d["category"] == "hook" and d["status"] == "failed"
    ]
    assert failed == [("eslint-typed", ESLINT_TYPED.node.version)]


def test_a_clean_project_pushes(installed, project):
    project.commit({"src/check.ts": AWAITED})
    code, shown = project.push()
    assert code == 0, shown
    assert (status(INSTALL, shown), status(TYPED, shown)) == ("Passed", "Passed")
    assert project.pushed() == project.git("rev-parse", "HEAD")
    gated = installed.cli(project.clone, "gate", env=project.env)
    assert gated["code"] == "checks_passed"
    hooks = [d["hook"] for d in gated["diagnostics"] if d["category"] == "hook"]
    assert hooks.index("gateseal-node-packages") < hooks.index("eslint-typed")
    for command in ("status", "check"):
        shown = installed.cli(project.clone, command, env=project.env)
        assert NOTE in [d["message"] for d in shown["diagnostics"]], command


def test_the_repository_s_eslint_config_and_suppressions_change_nothing(project):
    suppressions = {"src/check.ts": {RULE: {"count": 1}}}
    project.commit(
        {
            "src/check.ts": FLOATING,
            "eslint.config.js": THROWING,
            "eslint.config.mjs": THROWING,
            "eslint-suppressions.json": json.dumps(suppressions),
        }
    )
    code, shown = project.push()
    assert code != 0, shown
    assert status(TYPED, shown) == "Failed", shown
    assert RULE in shown
    assert "the repository's ESLint configuration ran" not in shown


def test_a_disable_comment_for_a_typed_rule_survives_the_commit(project):
    """The commit-time ESLint runs no typed rule, so its --fix would delete the
    comment as unused."""
    project.commit({"src/check.ts": DISABLED})
    assert (project.clone / "src/check.ts").read_text() == DISABLED
    code, shown = project.push()
    assert code == 0, shown
    assert status(TYPED, shown) == "Passed", shown


def test_only_the_files_that_tsconfig_includes_are_checked(project):
    """scripts/ is outside tsconfig.json, and ESLint checks an allowJs project's
    .js file only when the policy's files patterns name it."""
    tsconfig = json.loads((project.clone / "tsconfig.json").read_text())
    tsconfig["compilerOptions"]["allowJs"] = True
    (project.clone / "scripts").mkdir()
    project.commit(
        {
            "tsconfig.json": json.dumps(tsconfig, indent=2) + "\n",
            "src/view.js": "export const view = () => <main />;\n",
            "scripts/run.ts": FLOATING.replace("./shop.js", "../src/shop.js"),
        }
    )
    code, shown = project.push()
    assert code == 0, shown
    assert status(TYPED, shown) == "Passed", shown


@pytest.mark.parametrize("module_project", [NPM], ids=["npm"], indirect=True)
@pytest.mark.parametrize(
    ("tsconfig", "message"),
    [
        (None, "error TS5083: Cannot read file 'tsconfig.json'."),
        (
            '{"files": [], "references": [{"path": "./tsconfig.app.json"}]}\n',
            "tsconfig.json includes no file, and ESLint does not follow its "
            "references. Include the project's files in it.",
        ),
    ],
    ids=["missing", "only references"],
)
def test_a_tsconfig_without_files_stops_the_push(project, tsconfig, message):
    if tsconfig is None:
        (project.clone / "tsconfig.json").unlink()
        project.commit()
    else:
        app = (project.clone / "tsconfig.json").read_text()
        project.commit({"tsconfig.json": tsconfig, "tsconfig.app.json": app})
    code, shown = project.push()
    assert code != 0, shown
    assert status(TYPED, shown) == "Failed", shown
    assert message in shown
    assert "    at " not in shown
    assert project.pushed() == project.git("rev-parse", "adopted")


def test_a_lockfile_behind_package_json_stops_the_push_before_eslint(project):
    write_package(project.clone, project.manager, NEWER, lockfile=False)
    project.commit({"src/check.ts": AWAITED})
    code, shown = project.push()
    assert code != 0, shown
    assert (status(INSTALL, shown), status(TYPED, shown)) == ("Failed", None), shown


def test_configure_names_eslint_typed_without_a_project(installed, consumer):
    write_package(consumer, NPM, PINNED, lockfile=False)
    adopt = ("--capability", "typescript-typed-lint")
    refused = installed.cli(consumer, "configure", *adopt, code=2)
    assert refused["code"] == "missing_project"
    assert refused["diagnostics"][0]["message"].startswith(
        "eslint-typed runs in the repository's Node project, but the repository "
        "has neither package-lock.json nor pnpm-lock.yaml."
    )
    assert (
        "stop selecting typescript-typed-lint in gateseal.toml"
        in (refused["next_steps"][0])
    )
    assert not Path(consumer, "gateseal.toml").exists()
