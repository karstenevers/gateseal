"""knip finds unused files, exports and dependencies at a push and in the gate.

A consumer with an npm project, or a pnpm project for the clean push, selects
the bundled policy's typescript and typescript-unused capabilities. knip runs
as a node-project tool on the policy's Node, after gateseal's hook installs
the project's packages. Each push is a real git push to a bare repository
through the launcher that pre-commit wrote. typescript writes gateseal's
ESLint loader into .gateseal/, which knip must not report.

The first push on a test worker installs Node, knip and the project's packages
from the network.
"""

import json
import re

import pytest
from conftest import on_machine
from installation import clone, success
from node_projects import (
    EACH_KIND,
    MANAGERS,
    NEWER,
    NPM,
    write_package,
    write_project,
)
from team_policy import URL, environment, git, publish
from test_typescript_types import INSTALL, Project, status

pytestmark = pytest.mark.integration

ADOPT = ("--capability", "typescript", "--capability", "typescript-unused")
INDEX = """\
import { Item, label } from "./shop.js";

console.log(label(Item.parse({ name: "pen", cents: 5 })));
"""
"""The entry file that knip finds by its name, which uses all that shop.ts exports."""


HEADING = re.compile(r"(\S.*) \(\d+\)$")
"""A heading of knip's report, such as Unused files (2)."""


def unused(kind, shown):
    """Return the words of each line that knip printed under its heading kind."""
    found, rows = {}, None
    for line in shown.splitlines():
        heading = HEADING.fullmatch(line)
        if heading:
            rows = found.setdefault(heading[1], [])
        elif rows is not None and line.strip() and "...." not in line:
            rows.append(line.split())
        else:
            rows = None
    return found.get(kind)


@pytest.fixture(scope="module")
def module_project(request, installed, tmp_path_factory):
    """A project of the package manager that a test asks for, npm by default,
    adopted and pushed without its hooks."""
    manager = getattr(request, "param", NPM)
    root = tmp_path_factory.mktemp(f"knip {manager.name} consumer ü").resolve()
    remote = root / "remote.git"
    project = Project(root / "clone", remote, manager, installed.environment)
    with on_machine(installed):
        success(["git", "init", "--quiet", "--bare", "-b", "main", remote], root)
        success(["git", "clone", "--quiet", remote, project.clone], root)
        project.git("config", "user.name", "gateseal test")
        project.git("config", "user.email", "gateseal@example.invalid")
        write_project(project.clone, manager)
        (project.clone / "src/index.ts").write_text(INDEX)
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


@pytest.mark.parametrize(
    "module_project", MANAGERS, ids=lambda manager: manager.name, indirect=True
)
def test_a_clean_project_passes_knip_at_a_push(project):
    assert (project.clone / ".gateseal/eslint-loader.mjs").exists()
    committed = project.commit({"README.md": "# Shop\n"})
    assert status("knip", committed) is None, committed
    code, shown = project.push()
    assert code == 0, shown
    assert (status(INSTALL, shown), status("knip", shown)) == ("Passed", "Passed")
    assert project.pushed() == project.git("rev-parse", "HEAD")


def test_an_unused_dependency_stops_the_push(project):
    write_package(project.clone, NPM, EACH_KIND)
    project.commit()
    code, shown = project.push()
    assert code != 0, shown
    assert (status(INSTALL, shown), status("knip", shown)) == ("Passed", "Failed")
    assert unused("Unused devDependencies", shown) == [
        ["is-number", "package.json:11:6"]
    ]
    assert project.pushed() == project.git("rev-parse", "adopted")


def test_an_unused_export_and_file_stop_the_push(project):
    shop = (project.clone / "src/shop.ts").read_text()
    committed = project.commit(
        {
            "src/shop.ts": shop + "\nexport const unused = 1;\n",
            "src/old.ts": "export const old = 2;\n",
        }
    )
    assert status("knip", committed) is None, committed
    code, shown = project.push()
    assert code != 0, shown
    assert status("knip", shown) == "Failed", shown
    assert unused("Unused files", shown) == [["src/old.ts"]]
    assert unused("Unused exports", shown) == [["unused", "src/shop.ts:8:14"]]
    assert project.pushed() == project.git("rev-parse", "adopted")


def test_no_file_of_the_repository_changes_knip_s_settings(project):
    """knip would read knip.json, and the knip field of package.json even with
    --config."""
    ignore = {"ignoreFiles": ["src/old.ts"], "ignoreExportsUsedInFile": True}
    package = json.loads(NPM.package_json(EACH_KIND))
    package["knip"] = ignore | {"ignoreDependencies": ["is-number"]}
    write_package(project.clone, NPM, EACH_KIND)
    project.commit(
        {
            "package.json": json.dumps(package, indent=2) + "\n",
            "knip.json": json.dumps(ignore | {"ignore": ["src/**"]}),
            "src/old.ts": "export const old = 2;\nexport const used = old;\n",
        }
    )
    code, shown = project.push()
    assert code != 0, shown
    assert unused("Unused files", shown) == [["src/old.ts"]]
    assert unused("Unused devDependencies", shown)[0][0] == "is-number"


def test_the_knip_field_of_package_json_cannot_give_knip_a_prototype(project):
    """knip merges the field with Object.assign, which takes __proto__ as the
    prototype of the merged settings, so its keys would apply unseen."""
    package = json.loads(NPM.package_json(EACH_KIND))
    package["knip"] = {"__proto__": {"ignoreFiles": ["src/old.ts"]}}
    write_package(project.clone, NPM, EACH_KIND)
    project.commit(
        {
            "package.json": json.dumps(package, indent=2) + "\n",
            "src/old.ts": "export const old = 2;\nexport const used = old;\n",
        }
    )
    code, shown = project.push()
    assert code != 0, shown
    assert "Remove __proto__ from the knip field of package.json." in shown


def test_a_package_json_with_a_byte_order_mark_passes_knip(project):
    package = (project.clone / "package.json").read_text()
    project.commit({"package.json": "\ufeff" + package})
    code, shown = project.push()
    assert code == 0, shown
    assert status("knip", shown) == "Passed", shown


def test_the_policy_can_give_knip_one_ignore_pattern_as_a_string(installed, tmp_path):
    """knip takes a string or a list in ignore, and gateseal adds .gateseal/** to it."""
    origin = tmp_path / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(
        origin,
        "v1",
        lambda data: (data / "rules/knip.toml").write_text('ignore = "src/old.ts"\n'),
    )
    env = environment(installed, origin)
    consumer = clone(tmp_path / "consumer origin", tmp_path / "consumer")
    write_project(consumer, NPM)
    (consumer / "src/index.ts").write_text(INDEX)
    (consumer / "src/old.ts").write_text("export const old = 2;\n")
    source = ("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1")
    installed.cli(consumer, "configure", *ADOPT, *source, env=env)
    success(["git", "add", "-A"], consumer)
    success(["git", "commit", "-m", "Adopt"], consumer, env)
    gated = installed.cli(consumer, "gate", env=env)
    assert gated["code"] == "checks_passed"
    hooks = {d["hook"]: d["status"] for d in gated["diagnostics"] if "hook" in d}
    assert hooks["knip"] == "passed"


def test_a_lockfile_behind_package_json_stops_the_push_and_the_gate_before_knip(
    installed, project
):
    write_package(project.clone, NPM, NEWER, lockfile=False)
    project.commit()
    code, shown = project.push()
    assert code != 0, shown
    assert (status(INSTALL, shown), status("knip", shown)) == ("Failed", None), shown
    assert project.pushed() == project.git("rev-parse", "adopted")
    gated = installed.cli(project.clone, "gate", code=1, env=project.env)
    assert gated["code"] == "checks_failed"
    statuses = {d["hook"]: d["status"] for d in gated["diagnostics"] if "hook" in d}
    assert statuses["gateseal-node-packages"] == "failed"
    assert statuses == {"gateseal-node-packages": "failed"}


def test_the_gate_runs_knip_after_the_install(installed, project):
    gated = installed.cli(project.clone, "gate", env=project.env)
    assert gated["code"] == "checks_passed"
    hooks = {
        d["hook"]: d["status"] for d in gated["diagnostics"] if d["category"] == "hook"
    }
    assert list(hooks) == ["gateseal-node-packages", "eslint", "prettier", "knip"]
    assert set(hooks.values()) == {"passed"}
