"""A policy runs a tool that reads the repository's Node project: node-project.

The team policy of node_project_policy checks types with the Built-in Tool
tsc on the policy's Node. A fake upstream answers where gateseal policy lock
would ask nodejs.org and the npm registry, and a local Git repository serves
the policy's release to configure.
"""

import json
import shlex
import subprocess

import pytest
from gateseal import authoring, checks, lock
from gateseal.ci import Scope
from gateseal.lock import describe
from gateseal.policy import load_policy_tree
from gateseal.processes import Timed
from gateseal.tools import load_definition
from gateseal.tools.model import Origin
from gateseal.tools.node import Package
from node_policy import VERSION, released
from node_project_policy import (
    POLICY,
    TYPESCRIPT,
    VUE_TSC_DEFINITION,
    adopt,
    install_arguments,
    new_origin,
    project,
    publish,
    reach,
    registry,  # noqa: F401
    typed,
)
from test_lock_configure import preparation, refused, tracked
from test_npm_package import GENERATED
from test_team_tools import consumer

RECORD = {"package": "typescript", "packages": {}}
COMPILER = [Package("typescript", TYPESCRIPT)]


def configuration(locked):
    return json.loads(locked.snapshot.outputs[".pre-commit-config.yaml"])


def hooks(locked):
    """Return the hooks of each repository in the generated configuration."""
    config = configuration(locked)
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


def own_hooks(locked):
    """Return the hooks of gateseal's own repository, the configuration's first."""
    return configuration(locked)["repos"][0]["hooks"]


def locked_policy(tmp_path):
    root = typed(tmp_path)
    authoring.lock(root)
    return load_policy_tree(root)


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    origin = new_origin(tmp_path_factory, "node project policy")
    publish(origin, "v1")
    return origin


@pytest.fixture
def reached(origin, monkeypatch):
    reach(monkeypatch, origin)


def test_policy_lock_records_the_package_of_tsc(tmp_path, registry):  # noqa: F811
    root = typed(tmp_path)
    assert authoring.lock(root).code == "locked"
    components = json.loads((root / "policy.lock").read_bytes())["components"]
    assert components["tsc"] == {"versions": {TYPESCRIPT: RECORD}}
    assert released("typescript", TYPESCRIPT) in registry.downloaded
    registry.downloaded.clear()
    assert authoring.lock(root).changed == ()
    assert not [url for url in registry.downloaded if "registry" in url]


def test_a_typescript_release_the_registry_lacks_stops_the_lock(
    tmp_path,
    registry,  # noqa: F811
):
    del registry.files[released("typescript", TYPESCRIPT)]
    root = typed(tmp_path)
    failure = refused(authoring.lock, root)
    assert failure.code == "upstream_release_missing"
    assert str(failure) == (
        f"The npm registry has no release {TYPESCRIPT} of typescript, which tsc "
        f"{TYPESCRIPT} installs."
    )
    assert failure.next_step == (
        f"Check that typescript {TYPESCRIPT} is released, or remove it from "
        "policy.toml. Then run gateseal policy lock."
    )
    assert not (root / "policy.lock").exists()


def test_a_consumer_compiles_tsc_on_the_node_and_labels_it(tmp_path, registry):  # noqa: F811
    policy = locked_policy(tmp_path)
    locked, compiled = consumer(policy, ("hygiene", "typescript-types"))
    node = policy.tools["tsc"].node
    assert hooks(locked)["tsc"] == {
        "id": "tsc",
        "name": "tsc",
        "stages": ["pre-push", "manual"],
        "always_run": True,
        "pass_filenames": False,
        "exclude": GENERATED,
        "language": "unsupported",
        "entry": shlex.join(
            [*node.launch(COMPILER, "tsc"), "--noEmit", "--project", "tsconfig.json"]
        ),
        "require_serial": True,
    }
    assert own_hooks(locked) == [
        {"id": "gateseal-check"},
        {"id": "gateseal-lfs-check"},
        {
            "id": "gateseal-node-packages",
            "args": install_arguments(node),
            "stages": ["pre-push", "manual"],
        },
    ]
    assert list(hooks(locked)) == [
        "gateseal-check",
        "gateseal-lfs-check",
        "gateseal-node-packages",
        "check-yaml",
        "tsc",
    ]
    assert configuration(locked)["default_install_hook_types"] == [
        "pre-commit",
        "pre-push",
    ]
    assert compiled["components"]["tsc"] == {
        "package": "typescript",
        "version": TYPESCRIPT,
        "packages": {},
        "hook_ids": ["tsc"],
    }
    assert compiled["native_artifacts"]["node"]["version"] == VERSION
    assert [note.message for note in describe(locked) if note.category == "tool"] == [
        "Tool pre-commit-hooks 6.0.0 for hygiene: built-in, pre-commit-repository.",
        f"Tool tsc {TYPESCRIPT} for typescript-types: built-in, node-project on Node "
        f"{VERSION}; the repository's lockfile pins what it imports.",
    ]


@pytest.mark.parametrize(
    ("present", "message", "step"),
    [
        (
            ("package-lock.json",),
            "the repository has no package.json. Only npm and pnpm projects are "
            "supported.",
            "Create the project's package.json with npm or pnpm and commit it",
        ),
        (
            ("package.json",),
            "the repository has neither package-lock.json nor pnpm-lock.yaml. Only "
            "npm and pnpm projects are supported.",
            "Run npm install or pnpm install and commit the lockfile",
        ),
        (
            ("package.json", "package-lock.json", "pnpm-lock.yaml"),
            "the repository has both package-lock.json and pnpm-lock.yaml, so its "
            "package manager is not clear.",
            "Delete the lockfile that the project does not use and commit the change",
        ),
    ],
    ids=["no package.json", "no lockfile", "both lockfiles"],
)
def test_configure_needs_one_node_project_before_it_writes(
    tmp_path, reached, present, message, step
):
    root = project(tmp_path, *present)
    before = tracked(root)
    failure = refused(adopt, root, "hygiene", "typescript-types")
    assert failure.code in ("missing_project", "ambiguous_project")
    assert str(failure) == f"tsc runs in the repository's Node project, but {message}"
    assert failure.next_step == (
        f"{step}, or stop selecting typescript-types in gateseal.toml. Then run "
        "gateseal configure."
    )
    assert tracked(root) == before


@pytest.mark.parametrize("lockfile", ["package-lock.json", "pnpm-lock.yaml"])
def test_configure_takes_an_npm_or_a_pnpm_project(tmp_path, reached, lockfile):
    root = project(tmp_path, "package.json", lockfile)
    adopt(root, "hygiene", "typescript-types")
    loaded = lock.load(root, preparation(root))
    assert [tool.definition.name for tool in loaded.selection.tools][-1] == "tsc"


def test_check_stops_once_the_lockfile_is_gone(tmp_path, reached):
    root = project(tmp_path, "package.json", "pnpm-lock.yaml")
    adopt(root, "hygiene", "typescript-types")
    (root / "pnpm-lock.yaml").unlink()
    failure = refused(lock.load, root, preparation(root))
    assert failure.code == "missing_project"
    assert "tsc runs in the repository's Node project" in str(failure)


def test_a_selection_without_a_node_project_tool_needs_no_project(tmp_path, reached):
    root = project(tmp_path)
    adopt(root, "hygiene", "docs")
    loaded = lock.load(root, preparation(root))
    assert [tool.definition.name for tool in loaded.selection.tools] == [
        "pre-commit-hooks",
        "prettier-docs",
    ]


def test_the_first_node_project_tool_names_the_failure(tmp_path, reached):
    root = project(tmp_path)
    failure = refused(adopt, root, "vue-types", "typescript-types")
    assert str(failure).startswith("tsc runs in the repository's Node project")
    assert "stop selecting typescript-types in" in failure.next_step


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('package = "vue-tsc"', 'package = "vue-tsc@3"'),
        ('package = "vue-tsc"', 'package = "vue-tsc"\nregistry = "x"'),
        ('[executable]\nname = "vue-tsc"\n', ""),
    ],
    ids=["version", "unknown field", "no executable"],
)
def test_a_definition_reads_its_source_as_an_npm_package_tool_does(old, new):
    assert old in VUE_TSC_DEFINITION
    text = VUE_TSC_DEFINITION.replace(old, new, 1)
    failure = refused(load_definition, "x", text.encode(), Origin.TEAM)
    assert failure.code == "invalid_field"
    assert "tools.x" in str(failure) or "node-project tool" in str(failure)


def test_a_node_project_tool_scans_no_history():
    """A history scan would start even when the install of the packages failed."""
    text = VUE_TSC_DEFINITION + '\n[history]\nargs = ["--commits", "{range}"]\n'
    failure = refused(load_definition, "x", text.encode(), Origin.TEAM)
    assert (failure.code, str(failure)) == (
        "invalid_field",
        "A node-project tool cannot have tools.x.history.",
    )


def test_a_definition_names_its_npm_package():
    definition = load_definition("x", VUE_TSC_DEFINITION.encode(), Origin.TEAM)
    assert (definition.source.kind, definition.source.package) == (
        "node-project",
        "vue-tsc",
    )
    assert definition.source.on_node


def test_the_policy_pins_node_for_a_node_project_tool(tmp_path, registry):  # noqa: F811
    root = typed(tmp_path, POLICY.replace(f'node = "{VERSION}"\n', ""))
    failure = refused(authoring.lock, root)
    assert failure.code == "invalid_field"
    assert str(failure) == (
        "policy.toml needs node, the version of Node for tsc, vue-tsc, prettier-docs."
    )


@pytest.mark.parametrize(
    "capabilities",
    [("hygiene",), ("hygiene", "docs")],
    ids=["no tool on Node", "an npm-package tool"],
)
def test_a_selection_without_a_node_project_tool_installs_no_packages(
    tmp_path,
    registry,  # noqa: F811
    capabilities,
):
    locked, _ = consumer(locked_policy(tmp_path), capabilities)
    assert own_hooks(locked) == [{"id": "gateseal-check"}]


@pytest.mark.parametrize(
    ("capabilities", "stages"),
    [
        (("vue-types",), ["pre-commit", "manual"]),
        (("vue-types", "typescript-types"), ["pre-commit", "pre-push", "manual"]),
        (("typescript-types", "vue-types"), ["pre-commit", "pre-push", "manual"]),
    ],
)
def test_one_install_runs_in_every_stage_of_a_node_project_tool(
    tmp_path,
    registry,  # noqa: F811
    capabilities,
    stages,
):
    policy = locked_policy(tmp_path)
    locked, _ = consumer(policy, capabilities)
    installs = [h for h in own_hooks(locked) if h["id"] == "gateseal-node-packages"]
    assert installs == [
        {
            "id": "gateseal-node-packages",
            "args": install_arguments(policy.tools["vue-tsc"].node),
            "stages": stages,
        }
    ]
    assert hooks(locked)["vue-tsc"]["stages"] == ["pre-commit", "manual"]


FAILED_INSTALL = """\
Install the Node project's packages from its lockfile....................Failed
- hook id: gateseal-node-packages
- exit code: 1

npm error code EUSAGE
gateseal: npm could not install the packages that package-lock.json pins.
tsc......................................................................Passed
"""

PASSED = """\
Install the Node project's packages from its lockfile....................Passed
tsc......................................................................Passed
"""


def install_entry(stage, status):
    return {
        "severity": "error" if status == "Failed" else "info",
        "category": "hook",
        "tool": "gateseal",
        "origin": "built-in",
        "hook": "gateseal-node-packages",
        "stage": stage,
        "status": status.lower(),
        "modified": False,
        "message": f"Install the Node project's packages from its lockfile: {status}",
        "node": VERSION,
    }


def pre_commit_reports(monkeypatch, output, returncode):
    """Have pre-commit print output and exit with returncode, each line in 2 s."""

    def execute(command, root, env, allow_failure):
        return subprocess.CompletedProcess(command, returncode, output.encode(), b"")

    def execute_timed(command, root, env):
        lines = tuple((line.encode(), 2.0) for line in output.splitlines())
        return Timed(execute(command, root, env, True), 2.0 * len(lines), lines)

    monkeypatch.setattr(checks.setup, "require_pre_commit", lambda: "/python")
    monkeypatch.setattr(checks.fetch, "fetch", lambda locked, python: ())
    monkeypatch.setattr(checks, "execute", execute)
    monkeypatch.setattr(checks, "execute_timed", execute_timed)


@pytest.mark.parametrize(
    ("output", "returncode", "code", "status"),
    [
        (FAILED_INSTALL, 1, "checks_failed", "Failed"),
        (PASSED, 0, "checks_passed", "Passed"),
    ],
    ids=["failed install", "passed install"],
)
def test_a_failed_install_alone_fails_the_gate_and_shows_its_output(
    tmp_path,
    registry,  # noqa: F811
    monkeypatch,
    output,
    returncode,
    code,
    status,
):
    locked, _ = consumer(locked_policy(tmp_path), ("typescript-types",))
    pre_commit_reports(monkeypatch, output, returncode)
    gate = checks.gate(None, locked, Scope(None, None, None, "Checked every file."))
    assert gate.code == code
    assert gate.entries[1] == install_entry("ci", status)
    assert [
        (e["category"], e["hook"], e["status"], e["node"]) for e in gate.entries[2:]
    ] == [("hook", "tsc", "passed", VERSION)]
    assert "    2.0  Install the Node project's packages from its lockfile" in (
        gate.check_times
    )
    assert gate.details == output


def test_run_reports_the_install_where_a_commit_runs_it(
    tmp_path,
    registry,  # noqa: F811
    monkeypatch,
):
    locked, _ = consumer(locked_policy(tmp_path), ("vue-types",))
    output = FAILED_INSTALL.replace("tsc....", "vue-tsc")
    pre_commit_reports(monkeypatch, output, 1)
    run = checks.run(locked)
    assert run.code == "checks_failed"
    assert [e["hook"] for e in run.entries] == ["gateseal-node-packages", "vue-tsc"]
    assert run.entries[0] == install_entry("run", "Failed")
    assert run.details == output


def test_the_install_has_no_result_where_it_did_not_run(
    tmp_path,
    registry,  # noqa: F811
    monkeypatch,
):
    locked, _ = consumer(locked_policy(tmp_path), ("typescript-types",))
    pre_commit_reports(monkeypatch, PASSED.split("\n", 1)[1], 0)
    gate = checks.gate(None, locked, Scope(None, None, None, "Checked every file."))
    assert [e["hook"] for e in gate.entries[1:]] == ["tsc"]
