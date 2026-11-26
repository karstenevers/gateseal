"""The gateseal-node-packages hook installs a Node project's packages.

The hook starts the package manager on the Node that the Lock pins. A stand-in
for mise names a folder as that Node's install, and its node passes each
package manager's start to a script, which logs what it was asked to start. A
real install runs in no test.
"""

import json
import os
import sys
from pathlib import Path

import pytest
import yaml
from gateseal import node_packages
from gateseal.compiler_release import NODE_PACKAGES_HOOK, NODE_PACKAGES_HOOK_NAME
from gateseal.tools.package_managers import PNPM
from stand_ins import executable

STAND_IN = """\
import json
import os
import sys
from pathlib import Path

here = Path(__file__).parent
with (here / "started.jsonl").open("a") as log:
    log.write(json.dumps({"arguments": sys.argv[1:], "folder": os.getcwd()}) + "\\n")
print("out:", *sys.argv[1:])
print("err:", *sys.argv[1:], file=sys.stderr)
fail = here / "fail"
sys.exit(fail.exists() and fail.read_text() in sys.argv)
"""

PNPM_INSTALL = [
    *("install", "--frozen-lockfile", "--lockfile", "--ignore-scripts"),
    *("--ignore-pnpmfile",),
    *("--no-use-store-server", "--no-lockfile-only", "--lockfile-dir=."),
    *("--config.git-branch-lockfile=false",),
    *("--config.merge-git-branch-lockfiles=false",),
    *("--config.ignore-package-manifest=false", "--config.fix-lockfile=false"),
    *("--config.dedupe=false", "--config.update=false"),
    *("--modules-dir=node_modules", "--config.enable-modules-dir=true"),
    *("--config.symlink=true", "--config.filter=null", "--config.filter-prod=null"),
]


@pytest.fixture
def node(tmp_path):
    """The folder of the script that stands in for the pinned Node."""
    folder = tmp_path / "node"
    folder.mkdir()
    (folder / "node.py").write_text(STAND_IN)
    return folder


def project(tmp_path, *names):
    root = tmp_path / "project ü"
    root.mkdir()
    for name in names:
        (root / name).write_text("{}")
    return root


NODE = """\
#!{python}
import os
import subprocess
import sys
from pathlib import Path

program, *arguments = sys.argv[1:]
if program == "-e" or "--prefix" in arguments:
    sys.exit(0)
script = Path(__file__).parents[1] / "node.py"
name = Path(program).name.removesuffix(".cjs")
sys.exit(subprocess.run([sys.executable, script, name, *arguments]).returncode)
"""
"""The pinned Node: it passes a package manager's start to the script node.py
with the package manager's name first. The launcher's own first install of
pnpm, npm ci and its check, does nothing."""

LOCK = {"native_artifacts": {"node": {"version": "24.22.3", "platforms": {}}}}


def on_node(monkeypatch, root, node, mise="echo '{node}'", lock=LOCK):
    """Run the hook in root, with node as the install that mise names."""
    (root / "gateseal.lock").write_text(json.dumps(lock))
    executable(node / "bin/node", NODE.format(python=sys.executable))
    executable(node / "mise/mise", f"#!/bin/sh\n{mise.format(node=node)}\n")
    monkeypatch.setenv("PATH", f"{node / 'mise'}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("XDG_CACHE_HOME", str(node / "cache"))
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "argv", ["gateseal-node-packages"])
    return node_packages.main()


def started(node):
    log = node / "started.jsonl"
    lines = log.read_text().splitlines() if log.exists() else []
    return [json.loads(line) for line in lines]


def test_an_npm_project_proves_its_lockfile_then_installs_what_changed(
    tmp_path, node, monkeypatch
):
    root = project(tmp_path, "package.json", "package-lock.json")
    assert on_node(monkeypatch, root, node) == 0
    assert started(node) == [
        {
            "arguments": ["npm", "ci", "--dry-run", "--no-usage", "--ignore-scripts"],
            "folder": str(root),
        },
        {
            "arguments": [
                *("npm", "ls", "--package-lock-only", "--include=dev"),
                *("--include=optional", "--include=peer", "--no-usage"),
            ],
            "folder": str(root),
        },
        {
            "arguments": [
                *("npm", "install", "--no-save", "--package-lock"),
                *("--no-package-lock-only", "--no-dry-run", "--no-usage"),
                *("--ignore-scripts", "--no-audit"),
            ],
            "folder": str(root),
        },
    ]


REWRITES_ITS_RECORD = """\
import json
import sys
from pathlib import Path

record = Path("node_modules/.package-lock.json")
held = [record.read_text(), record.stat().st_mtime_ns] if record.exists() else None
with (Path(__file__).parent / "started.jsonl").open("a") as log:
    log.write(json.dumps({"command": sys.argv[2], "record": held}) + "\\n")
if "--dry-run" in sys.argv:
    record.parent.mkdir(exist_ok=True)
    record.write_text("what the lockfile pins")
fail = Path(__file__).parent / "fail"
sys.exit(fail.exists() and fail.read_text() in sys.argv)
"""
"""A stand-in for npm whose dry run records a node_modules it did not install."""

LAST_INSTALL = ["what the last install left", 1_000_000_000_000_000_000]


def last_install(root):
    """Write npm's record of the last install, with an old time, like npm."""
    record = root / "node_modules/.package-lock.json"
    record.parent.mkdir()
    record.write_text(LAST_INSTALL[0])
    os.utime(record, ns=(LAST_INSTALL[1], LAST_INSTALL[1]))
    return record


def test_npm_installs_against_the_record_of_its_last_install(
    tmp_path, node, monkeypatch
):
    """npm install replaces a package whose source changed only when its record
    names what the last install left, and npm trusts the record only when it is
    older than each folder of node_modules."""
    (node / "node.py").write_text(REWRITES_ITS_RECORD)
    root = project(tmp_path, "package.json", "package-lock.json")
    last_install(root)
    assert on_node(monkeypatch, root, node) == 0
    assert started(node) == [
        {"command": "ci", "record": LAST_INSTALL},
        {"command": "ls", "record": LAST_INSTALL},
        {"command": "install", "record": LAST_INSTALL},
    ]


def test_without_a_record_npm_installs_without_the_dry_run_s(
    tmp_path, node, monkeypatch
):
    (node / "node.py").write_text(REWRITES_ITS_RECORD)
    root = project(tmp_path, "package.json", "package-lock.json")
    assert on_node(monkeypatch, root, node) == 0
    assert started(node) == [
        {"command": "ci", "record": None},
        {"command": "ls", "record": None},
        {"command": "install", "record": None},
    ]


def test_a_failed_dry_run_leaves_the_record_of_the_last_install(
    tmp_path, node, monkeypatch
):
    (node / "node.py").write_text(REWRITES_ITS_RECORD)
    root = project(tmp_path, "package.json", "package-lock.json")
    record = last_install(root)
    (node / "fail").write_text("ci")
    assert on_node(monkeypatch, root, node) == 1
    assert [record.read_text(), record.stat().st_mtime_ns] == LAST_INSTALL


LOGS_ITS_ENVIRONMENT = """\
import json
import os
import sys
from pathlib import Path

names = (
    "npm_config_git",
    "npm_config_node_options",
    "npm_config_location",
    "npm_config_global",
    "GATESEAL_TEST_KEPT",
)
start = {"command": sys.argv[2], "environment": [os.environ.get(n) for n in names]}
with (Path(__file__).parent / "started.jsonl").open("a") as log:
    log.write(json.dumps(start) + "\\n")
"""
"""A stand-in for npm that logs the settings that its environment holds."""


def test_npm_and_the_npm_it_starts_take_git_from_path_and_install_in_the_project(
    tmp_path, node, monkeypatch
):
    """npm starts npm again for a git dependency, and only the environment reaches
    that npm."""
    (node / "node.py").write_text(LOGS_ITS_ENVIRONMENT)
    monkeypatch.setenv("npm_config_git", "./git-marker")
    monkeypatch.setenv("npm_config_node_options", "--require=./marker.cjs")
    monkeypatch.setenv("npm_config_location", "global")
    monkeypatch.setenv("npm_config_global", "true")
    monkeypatch.setenv("GATESEAL_TEST_KEPT", "kept")
    root = project(tmp_path, "package.json", "package-lock.json")
    assert on_node(monkeypatch, root, node) == 0
    assert started(node) == [
        {"command": command, "environment": ["git", "null", "project", "false", "kept"]}
        for command in ("ci", "ls", "install")
    ]


@pytest.mark.parametrize(("check", "ran"), [("ci", ["ci"]), ("ls", ["ci", "ls"])])
def test_an_npm_lockfile_that_does_not_match_stops_before_the_install(
    tmp_path, node, monkeypatch, capfd, check, ran
):
    root = project(tmp_path, "package.json", "package-lock.json")
    (node / "fail").write_text(check)
    assert on_node(monkeypatch, root, node) == 1
    assert [s["arguments"][1] for s in started(node)] == ran
    assert capfd.readouterr().err.endswith(
        "gateseal: npm could not install the packages that package-lock.json pins.\n"
        "Next: If package-lock.json does not match package.json, run npm install "
        "and commit package-lock.json. Otherwise read the output of npm above. Then "
        "push again.\n"
    )


def test_a_pnpm_project_starts_pnpm_from_its_pinned_lockfile(
    tmp_path, node, monkeypatch
):
    root = project(tmp_path, "package.json", "pnpm-lock.yaml")
    assert on_node(monkeypatch, root, node) == 0
    assert started(node) == [
        {
            "arguments": ["pnpm", *PNPM_INSTALL],
            "folder": str(root),
        }
    ]
    [install] = (node / "cache/gateseal/npm").glob("*/node-24.22.3/*")
    assert (install / "package-lock.json").read_bytes() == PNPM.tree.lockfile
    assert not list(root.glob("*npmrc*"))


def test_a_failed_pnpm_install_names_pnpm_and_its_lockfile(
    tmp_path, node, monkeypatch, capfd
):
    root = project(tmp_path, "package.json", "pnpm-lock.yaml")
    (node / "fail").write_text("install")
    assert on_node(monkeypatch, root, node) == 1
    assert capfd.readouterr().err.endswith(
        "gateseal: pnpm could not install the packages that pnpm-lock.yaml pins.\n"
        "Next: If pnpm-lock.yaml does not match package.json, run pnpm install and "
        "commit pnpm-lock.yaml. Otherwise read the output of pnpm above. Then push "
        "again.\n"
    )


def test_the_install_output_reaches_the_hook_output(tmp_path, node, monkeypatch, capfd):
    root = project(tmp_path, "package.json", "pnpm-lock.yaml")
    assert on_node(monkeypatch, root, node) == 0
    shown = " ".join(["pnpm", *PNPM_INSTALL])
    assert capfd.readouterr() == (f"out: {shown}\n", f"err: {shown}\n")


def test_a_repository_without_a_lockfile_starts_nothing(
    tmp_path, node, monkeypatch, capfd
):
    root = project(tmp_path, "package.json")
    assert on_node(monkeypatch, root, node) == 1
    assert started(node) == []
    assert capfd.readouterr() == (
        "",
        "gateseal: A selected check runs in the repository's Node project, but the "
        "repository has neither package-lock.json nor pnpm-lock.yaml. Only npm and "
        "pnpm projects are supported.\n"
        "Next: Run npm install or pnpm install and commit the lockfile.\n",
    )


NO_NODE = '[ "$2" = exec ] || exit 1'
"""mise with its auto-install off: exec installs nothing, and where fails."""


@pytest.mark.parametrize("lockfile", ["package-lock.json", "pnpm-lock.yaml"])
def test_without_the_pinned_node_the_hook_stops_and_names_it(
    tmp_path, node, monkeypatch, capfd, lockfile
):
    root = project(tmp_path, "package.json", lockfile)
    assert on_node(monkeypatch, root, node, NO_NODE) == 1
    assert started(node) == []
    assert capfd.readouterr().err.endswith(
        "mise has no install of Node 24.22.3, the pinned Node. "
        "Turn the auto-install of mise on.\n"
    )


def test_without_a_lock_that_records_node_the_hook_stops(
    tmp_path, node, monkeypatch, capfd
):
    root = project(tmp_path, "package.json", "package-lock.json")
    assert on_node(monkeypatch, root, node, lock={}) == 1
    assert started(node) == []
    assert capfd.readouterr() == (
        "",
        "gateseal.lock does not record the pinned Node. Run gateseal configure.\n",
    )


def test_the_repository_declares_a_hook_without_stages_that_stops_later_hooks():
    declared = yaml.safe_load(
        (Path(__file__).parents[1] / ".pre-commit-hooks.yaml").read_text()
    )
    entry = next(hook for hook in declared if hook["id"] == NODE_PACKAGES_HOOK)
    assert {key: value for key, value in entry.items() if key != "description"} == {
        "id": NODE_PACKAGES_HOOK,
        "name": NODE_PACKAGES_HOOK_NAME,
        "entry": "gateseal-node-packages",
        "language": "python",
        "pass_filenames": False,
        "always_run": True,
        "fail_fast": True,
    }
