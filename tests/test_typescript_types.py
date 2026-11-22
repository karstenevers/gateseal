"""tsc checks the types of a Node project at a push and in the gate.

A consumer with an npm or a pnpm project selects the bundled policy's
typescript-types capability, which runs the Built-in Tool tsc on the policy's
Node. Each push is a real git push to a bare repository through the launcher
that pre-commit wrote. One more consumer adopts a team policy that runs a
Team-defined Tool of the same kind at each commit.

The first push on a test worker installs Node, tsc and the project's packages
from the network. Later pushes on that worker find them in the home that
gateseal setup ran in, where mise keeps Node, npm its cache and pnpm its
store.
"""

import base64
import hashlib
import io
import json
import re
import shlex
import tarfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from string import Template

import pytest
from conftest import BUNDLED_TOOLS, on_machine
from gateseal.compiler_release import NODE_PACKAGES_HOOK
from gateseal.validation import canonical
from installation import clone, process, success
from node_projects import (
    DEPRECATED_OPTION,
    DEVELOPMENT,
    DEVICE,
    EACH_KIND,
    MANAGERS,
    NEWER,
    NEWER_ZOD,
    NPM,
    NPM_EACH_KIND_WITHOUT_PEERS,
    NPM_GIT_DEPENDENCIES,
    OPTIONAL,
    OWN_COMPILER,
    OWN_TYPESCRIPT,
    PEER,
    PINNED,
    PNPM,
    REMOVED_OPTION,
    TOTAL,
    TSCONFIG,
    WRONG_TOTAL,
    ZOD,
    Manager,
    write_package,
    write_project,
)
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

TSC = BUNDLED_TOOLS["tsc"]
TYPESCRIPT = TSC.pin
NODE = TSC.node.version
OPT_IN = "7.0.2"
"""The reviewed TypeScript that a repository selects through a Version Exception."""
ADOPT = ("--capability", "typescript-types")
CHECK = "Check files and tools against gateseal.lock"
INSTALL = "Install the Node project's packages from its lockfile"
WRONG_TOTAL_ERROR = (
    "src/total.ts(3,46): error TS2322: Type 'number' is not assignable to type "
    "'string'."
)
NOTE = (
    f"Tool tsc {TYPESCRIPT} for typescript-types: built-in, node-project on Node "
    f"{NODE}; the repository's lockfile pins what it imports."
)
TYPECHECK = """\
kind = "node-project"
stages = ["commit", "run", "ci"]
scope = "project"

[source]
package = "typescript"

[executable]
name = "tsc"

[hooks.typecheck]
name = "typecheck"
args = ["--noEmit", "--project", "tsconfig.json", "--strict"]
output = "plain"
"""
TEAM_TYPES = f"""
[capabilities.team-types.tools.typecheck]
pin = "{TYPESCRIPT}"
reviewed = ["{TYPESCRIPT}"]
"""


def status(name, shown):
    """Return the status that pre-commit printed for the hook name, or None."""
    found = re.search(rf"^{re.escape(name)}\.+(Passed|Failed)$", shown, re.MULTILINE)
    return found and found[1]


@dataclass(frozen=True)
class Project:
    """A clone of an adopted Node project, and the bare repository it pushes to."""

    clone: Path
    remote: Path
    manager: Manager
    env: dict[str, str]

    def git(self, *arguments):
        return success(["git", *arguments], self.clone, self.env).stdout.strip()

    def commit(self, files=None):
        """Write files, commit every change, and return git commit's output."""
        for name, text in (files or {}).items():
            (self.clone / name).write_text(text)
        self.git("add", "-A")
        committed = process(["git", "commit", "-m", "Change"], self.clone, self.env)
        shown = committed.stdout + committed.stderr
        assert committed.returncode == 0, shown
        return shown

    def push(self):
        pushed = process(["git", "push", "origin", "main"], self.clone, self.env)
        return pushed.returncode, pushed.stdout + pushed.stderr

    def pushed(self):
        """Return the commit that the remote's main names."""
        return success(["git", "rev-parse", "main"], self.remote).stdout.strip()

    def installed(self, package):
        """Return the version of package in node_modules, or None."""
        manifest = self.clone / "node_modules" / package / "package.json"
        if not manifest.exists():
            return None
        return json.loads(manifest.read_text())["version"]


@pytest.fixture(scope="module", params=MANAGERS, ids=lambda manager: manager.name)
def module_project(request, installed, tmp_path_factory):
    """A project of each package manager, adopted and pushed without its hooks."""
    manager = request.param
    root = tmp_path_factory.mktemp(f"{manager.name} consumer ü").resolve()
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


def test_a_push_installs_the_packages_that_tsc_reads(project):
    assert project.installed("zod") is None
    project.commit({"src/total.ts": TOTAL})
    code, shown = project.push()
    assert code == 0, shown
    assert (status(INSTALL, shown), status("tsc", shown)) == ("Passed", "Passed")
    assert project.pushed() == project.git("rev-parse", "HEAD")
    assert project.installed("zod") == ZOD


def test_a_type_error_commits_without_tsc_and_stops_the_push(project):
    committed = project.commit({"src/total.ts": WRONG_TOTAL})
    assert status(CHECK, committed) == "Passed", committed
    assert (status(INSTALL, committed), status("tsc", committed)) == (None, None)
    assert project.installed("zod") is None
    code, shown = project.push()
    assert code != 0, shown
    assert status("tsc", shown) == "Failed", shown
    assert WRONG_TOTAL_ERROR in shown
    assert project.pushed() == project.git("rev-parse", "adopted")


REPOSITORY_PROGRAMS = {
    "node": "#!/bin/sh\necho 'the repository node ran'\nexit 96\n",
    "sh": "#!/bin/sh\necho 'the repository sh ran'\nexit 95\n",
}


def test_a_program_among_the_project_s_packages_replaces_no_node_and_no_shell(project):
    """npm puts node_modules/.bin on PATH, and the pinned Node still runs tsc."""
    project.commit({"src/total.ts": TOTAL})
    code, shown = project.push()
    assert code == 0, shown
    programs = project.clone / "node_modules" / ".bin"
    programs.mkdir(exist_ok=True)
    for name, text in REPOSITORY_PROGRAMS.items():
        (programs / name).write_text(text)
        (programs / name).chmod(0o755)
    project.commit({"src/total.ts": WRONG_TOTAL})
    code, shown = project.push()
    assert code != 0, shown
    assert status(INSTALL, shown) == "Passed", shown
    assert WRONG_TOTAL_ERROR in shown
    assert "the repository" not in shown
    assert sorted(
        path.name for path in programs.iterdir() if path.name in REPOSITORY_PROGRAMS
    ) == ["node", "sh"]


def test_a_push_replaces_stale_packages_with_the_lockfile_s(project):
    """device.ts passes tsc only with the newer zod's types."""
    project.commit({"src/total.ts": TOTAL})
    code, shown = project.push()
    assert code == 0, shown
    write_package(project.clone, project.manager, NEWER)
    project.commit({"src/device.ts": DEVICE})
    assert project.installed("zod") == ZOD
    code, shown = project.push()
    assert code == 0, shown
    assert project.installed("zod") == NEWER_ZOD


PRICE = """\
import type { Cents } from "money";

export const price: Cents = 5;
"""
VENDORED = {
    "npm": Template("""\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "money": "$source"
      }
    },
    "node_modules/money": {
      "version": "1.0.0",
      "resolved": "$source",
      "integrity": "$integrity"
    }
  }
}
"""),
    "pnpm": Template("""\
lockfileVersion: '9.0'

settings:
  autoInstallPeers: true
  excludeLinksFromLockfile: false

importers:

  .:
    dependencies:
      money:
        specifier: $source
        version: $source

packages:

  money@$source:
    resolution: {integrity: $integrity, tarball: $source}
    version: 1.0.0

snapshots:

  money@$source: {}
"""),
}
"""The lockfile of one tarball in vendor, as npm 11 and pnpm 10 write it."""


def vendor(project, name, declaration):
    """Pack money 1.0.0 with declaration as vendor/name, and make it the project's
    only dependency."""
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as archive:
        manifest = {"name": "money", "version": "1.0.0", "types": "index.d.ts"}
        for member, text in {
            "package/package.json": json.dumps(manifest),
            "package/index.d.ts": declaration,
        }.items():
            info = tarfile.TarInfo(member)
            info.size = len(text.encode())
            archive.addfile(info, io.BytesIO(text.encode()))
    (project.clone / "vendor").mkdir(exist_ok=True)
    (project.clone / "vendor" / name).write_bytes(packed.getvalue())
    source = f"file:vendor/{name}"
    digest = base64.b64encode(hashlib.sha512(packed.getvalue()).digest()).decode()
    package = json.loads(project.manager.package_json(PINNED))
    package["dependencies"] = {"money": source}
    (project.clone / "package.json").write_text(json.dumps(package, indent=2))
    (project.clone / project.manager.lockfile).write_text(
        VENDORED[project.manager.name].substitute(
            source=source, integrity=f"sha512-{digest}"
        )
    )


def test_a_push_replaces_a_package_whose_new_tarball_keeps_its_version(project):
    """npm's record of its last install names money 1.0.0 both times."""
    (project.clone / "src" / "shop.ts").unlink()
    vendor(project, "money-a.tgz", "export type Cents = number;\n")
    project.commit({"src/price.ts": PRICE})
    code, shown = project.push()
    assert code == 0, shown
    (project.clone / "vendor" / "money-a.tgz").unlink()
    vendor(project, "money-b.tgz", "export type Cents = string;\n")
    project.commit()
    code, shown = project.push()
    assert code != 0, shown
    assert status(INSTALL, shown) == "Passed", shown
    assert (
        "src/price.ts(3,14): error TS2322: Type 'number' is not assignable to type "
        "'string'."
    ) in shown


def test_a_lockfile_that_names_another_tarball_of_the_version_stops_the_push(
    project,
):
    """npm ci compares only versions. package.json names the tarball whose types
    pass, and the lockfile pins the other."""
    (project.clone / "src" / "shop.ts").unlink()
    vendor(project, "money-b.tgz", "export type Cents = number;\n")
    vendor(project, "money-a.tgz", "export type Cents = string;\n")
    package = project.clone / "package.json"
    package.write_text(package.read_text().replace("money-a.tgz", "money-b.tgz"))
    project.commit({"src/price.ts": PRICE})
    code, shown = project.push()
    assert code != 0, shown
    assert (status(INSTALL, shown), status("tsc", shown)) == ("Failed", None), shown
    assert project.pushed() == project.git("rev-parse", "adopted")


@pytest.mark.parametrize("module_project", [NPM], ids=["npm"], indirect=True)
def test_a_push_without_a_root_tsconfig_stops_at_tsc_whatever_is_above(project):
    """Without --project, tsc would check what a tsconfig.json above names."""
    above = project.clone.parent
    (above / "tsconfig.json").write_text('{"files": ["empty.ts"]}\n')
    (above / "empty.ts").write_text("export {};\n")
    try:
        (project.clone / "tsconfig.json").unlink()
        project.commit({"src/total.ts": WRONG_TOTAL})
        code, shown = project.push()
    finally:
        (above / "tsconfig.json").unlink()
        (above / "empty.ts").unlink()
    assert code != 0, shown
    assert status("tsc", shown) == "Failed", shown
    assert "error TS5058: The specified path does not exist: 'tsconfig.json'." in shown
    assert project.pushed() == project.git("rev-parse", "adopted")


NPM_SETTINGS = {
    "lockfile-off": "package-lock=false\ndry-run=true\n"
    "omit[]=dev\nomit[]=optional\nomit[]=peer\nusage=true\nglobal=true\n",
    "lockfile-only": "package-lock-only=true\nlocation=global\n",
}
"""Settings of .npmrc that each make npm leave out part of the lockfile or stop.

package-lock-only=true in an .npmrc makes npm read the lockfile even when the
command line turns package-lock-only off, so it would hide package-lock=false.
global=true and location=global each make both npm commands work on the global
folder of Node instead of the project.
"""


@pytest.mark.parametrize("module_project", [NPM], ids=["npm"], indirect=True)
@pytest.mark.parametrize("npmrc", NPM_SETTINGS.values(), ids=NPM_SETTINGS.keys())
def test_npm_installs_all_that_the_lockfile_pins_whatever_npmrc_says(project, npmrc):
    write_package(project.clone, NPM, EACH_KIND)
    project.commit({".npmrc": npmrc})
    code, shown = project.push()
    assert code == 0, shown
    assert status(INSTALL, shown) == "Passed", shown
    pins = {"zod": ZOD} | DEVELOPMENT | OPTIONAL | PEER
    assert {name: project.installed(name) for name in pins} == pins


@pytest.mark.parametrize("module_project", [NPM], ids=["npm"], indirect=True)
def test_npm_installs_no_peer_that_a_lockfile_without_peers_leaves_out(project):
    """npm wrote the lockfile with the legacy-peer-deps that the project commits."""
    write_package(project.clone, NPM, EACH_KIND)
    project.commit(
        {
            ".npmrc": "legacy-peer-deps=true\n",
            "package-lock.json": NPM_EACH_KIND_WITHOUT_PEERS,
        }
    )
    code, shown = project.push()
    assert code == 0, shown
    assert status(INSTALL, shown) == "Passed", shown
    pins = {"zod": ZOD, "@types/react-dom": "19.2.3"} | DEVELOPMENT | OPTIONAL
    pins |= dict.fromkeys([*PEER, "csstype"])
    assert {name: project.installed(name) for name in pins} == pins


MARKER = """\
#!/bin/sh
: > {ran}/{name}
exec git "$@"
"""
"""A program in place of git that leaves a file named name in the folder ran."""


def git_package(folder, ran, files):
    """Commit files as a package in a bare repository; return its path and commit."""
    folder.mkdir()
    for name, text in files.items():
        (folder / name).write_text(text)
    (folder / "git-marker").write_text(MARKER.format(ran=ran, name=folder.name))
    (folder / "git-marker").chmod(0o755)
    author = ("-c", "user.name=gateseal test", "-c", "user.email=t@example.invalid")
    success(["git", "init", "--quiet", "-b", "main"], folder)
    success(["git", "add", "-A"], folder)
    success(["git", *author, "commit", "--quiet", "-m", "Package"], folder)
    bare = folder.with_suffix(".git")
    success(["git", "clone", "--quiet", "--bare", folder, bare], folder.parent)
    return bare, success(["git", "rev-parse", "HEAD"], folder).stdout.strip()


@pytest.mark.parametrize("module_project", [NPM], ids=["npm"], indirect=True)
@pytest.mark.parametrize("named_by", ["project", "outer"])
def test_npm_runs_no_program_that_an_npmrc_names_for_git_dependencies(
    project, tmp_path, named_by
):
    """npm installs outer's dependencies in a clone of outer, because outer has a
    script, and that npm reads the clone's .npmrc. The project's node-options
    reaches that npm too."""
    ran = tmp_path / "ran"
    ran.mkdir()
    (tmp_path / "node-marker.cjs").write_text(
        f"require('fs').writeFileSync('{ran}/node-options', '');\n"
    )
    npmrc = {"project": f"node-options=--require={tmp_path}/node-marker.cjs\n"}
    npmrc[named_by] = "git=./git-marker\n" + npmrc.get(named_by, "")
    script = f"node -e \"require('fs').writeFileSync('{ran}/prepare', '')\""
    inner_package = {
        "name": "inner",
        "version": "1.0.0",
        "scripts": {"prepare": script},
    }
    inner, inner_commit = git_package(
        tmp_path / "inner", ran, {"package.json": json.dumps(inner_package)}
    )
    outer_package = inner_package | {
        "name": "outer",
        "dependencies": {"inner": f"git+file://{inner}"},
    }
    outer_files = {"package.json": json.dumps(outer_package)}
    outer, outer_commit = git_package(
        tmp_path / "outer", ran, outer_files | {".npmrc": npmrc.get("outer", "")}
    )
    package = json.loads(NPM.package_json(PINNED))
    package["dependencies"]["outer"] = f"git+file://{outer}"
    (project.clone / "git-marker").write_text(MARKER.format(ran=ran, name="project"))
    (project.clone / "git-marker").chmod(0o755)
    lockfile = NPM_GIT_DEPENDENCIES.substitute(
        outer=outer, outer_commit=outer_commit, inner=inner, inner_commit=inner_commit
    )
    project.commit(
        {
            "package.json": json.dumps(package),
            "package-lock.json": lockfile,
            ".npmrc": npmrc["project"],
        }
    )
    code, shown = project.push()
    assert sorted(path.name for path in ran.iterdir()) == []
    assert code == 0, shown
    assert status(INSTALL, shown) == "Passed", shown
    assert (project.installed("outer"), project.installed("inner")) == ("1.0.0",) * 2


PNPM_SETTINGS = """\
only=prod
dev=true
production=true
optional=false
lockfile-only=true
lockfile=false
lockfile-dir=..
use-store-server=true
modules-dir=nm
enable-modules-dir=false
symlink=false
node-version=1.0.0
package-import-method=clone
"""
"""Settings of .npmrc that each make pnpm install leave out part of the lockfile.

use-store-server=true runs the pnpmfile in a store server, which keeps the push
waiting. package-import-method=clone fails where the file system cannot clone.
"""
PNPM_WORKSPACE = """\
filter:
  - nothing
filterProd:
  - nothing
resolutionOnly: true
update: true
"""
"""Settings of pnpm-workspace.yaml that make pnpm install no project.

resolutionOnly makes pnpm refuse the frozen install instead, and update makes it
install newer releases than the lockfile pins.
"""
PNPMFILE = """\
require("fs").writeFileSync(`${__dirname}/pnpmfile-ran`, "");
module.exports = { hooks: {} };
"""


@pytest.mark.parametrize("module_project", [PNPM], ids=["pnpm"], indirect=True)
def test_pnpm_installs_all_that_the_lockfile_pins_and_runs_no_pnpmfile(project):
    write_package(project.clone, PNPM, EACH_KIND)
    project.commit(
        {
            ".npmrc": PNPM_SETTINGS,
            "pnpm-workspace.yaml": PNPM_WORKSPACE,
            ".pnpmfile.cjs": PNPMFILE,
        }
    )
    code, shown = project.push()
    assert not (project.clone / "pnpmfile-ran").exists()
    assert code == 0, shown
    assert status(INSTALL, shown) == "Passed", shown
    pins = {"zod": ZOD} | DEVELOPMENT | OPTIONAL
    assert {name: project.installed(name) for name in pins} == pins


STALE_LOCKFILE_SETTINGS = {
    "git-branch-lockfile": (".npmrc", "git-branch-lockfile=true\n"),
    "gitBranchLockfile": ("pnpm-workspace.yaml", "gitBranchLockfile: true\n"),
    "ignorePackageManifest": ("pnpm-workspace.yaml", "ignorePackageManifest: true\n"),
    "fixLockfile": ("pnpm-workspace.yaml", "fixLockfile: true\n"),
    "dedupe": ("pnpm-workspace.yaml", "dedupe: true\n"),
}
"""Settings that each make pnpm pass over a pnpm-lock.yaml behind package.json.

A branch lockfile makes pnpm read pnpm-lock.main.yaml on the branch main in
place of pnpm-lock.yaml. ignorePackageManifest makes pnpm install from the
lockfile without a look at package.json. fixLockfile and dedupe make pnpm
rewrite pnpm-lock.yaml to match package.json.
"""


@pytest.mark.parametrize("module_project", [PNPM], ids=["pnpm"], indirect=True)
@pytest.mark.parametrize("setting", STALE_LOCKFILE_SETTINGS)
def test_pnpm_stops_at_a_pnpm_lock_yaml_behind_package_json(project, setting):
    write_package(project.clone, PNPM, NEWER, lockfile=False)
    name, text = STALE_LOCKFILE_SETTINGS[setting]
    project.commit({name: text, "pnpm-lock.main.yaml": PNPM.locks[NEWER]})
    code, shown = project.push()
    assert code != 0, shown
    assert (status(INSTALL, shown), status("tsc", shown)) == ("Failed", None), shown
    assert project.pushed() == project.git("rev-parse", "adopted")
    assert project.git("status", "--porcelain", "pnpm-lock.yaml") == ""


def pnpm(project, *arguments):
    """Run the pnpm that the install hook starts, with the project's environment."""
    config = json.loads((project.clone / ".pre-commit-config.yaml").read_text())
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    [hook] = [hook for hook in hooks if hook["id"] == NODE_PACKAGES_HOOK]
    [start] = [a.removeprefix("pnpm=") for a in hook["args"] if a.startswith("pnpm=")]
    success([*shlex.split(start), *arguments], project.clone, project.env)


@pytest.mark.parametrize("module_project", [PNPM], ids=["pnpm"], indirect=True)
def test_pnpm_replaces_node_modules_without_development_dependencies(project):
    """pnpm would ask before it recreates node_modules, and a push has no terminal."""
    write_package(project.clone, PNPM, EACH_KIND)
    project.commit()
    pnpm(project, "install", "--prod", "--frozen-lockfile", "--ignore-scripts")
    assert project.installed("is-number") is None
    code, shown = project.push()
    assert code == 0, shown
    pins = {"zod": ZOD} | DEVELOPMENT | OPTIONAL
    assert {name: project.installed(name) for name in pins} == pins


def test_a_lockfile_behind_package_json_stops_the_push_and_the_gate_before_tsc(
    installed, project
):
    """usage=true would make npm ci print its usage and pass without a check."""
    name, lockfile = project.manager.name, project.manager.lockfile
    write_package(project.clone, project.manager, NEWER, lockfile=False)
    project.commit({".npmrc": "usage=true\n"})
    code, shown = project.push()
    assert code != 0, shown
    assert (status(INSTALL, shown), status("tsc", shown)) == ("Failed", None), shown
    assert (
        f"gateseal: {name} could not install the packages that {lockfile} pins.\n"
        f"Next: If {lockfile} does not match package.json, run {name} install and "
        f"commit {lockfile}. Otherwise read the output of {name} above. Then push "
        "again.\n"
    ) in shown
    assert project.pushed() == project.git("rev-parse", "adopted")
    gated = installed.cli(project.clone, "gate", code=1, env=project.env)
    assert gated["code"] == "checks_failed"
    [entry] = [d for d in gated["diagnostics"] if d["category"] == "hook"]
    assert (entry["hook"], entry["status"]) == ("gateseal-node-packages", "failed")


def test_tsc_is_the_policy_s_release_and_not_the_project_s_own(project):
    """The project's own TypeScript reads baseUrl, which the policy's deprecates."""
    write_package(project.clone, project.manager, OWN_COMPILER)
    project.commit({"tsconfig.json": REMOVED_OPTION})
    code, shown = project.push()
    assert code != 0, shown
    assert (
        "tsconfig.json(8,5): error TS5101: Option 'baseUrl' is deprecated and will "
        "stop functioning in TypeScript 7.0."
    ) in shown
    assert project.installed("typescript") == OWN_TYPESCRIPT


def test_a_version_exception_runs_the_typescript_that_it_selects(installed, project):
    project.commit({"tsconfig.json": DEPRECATED_OPTION})
    code, shown = project.push()
    assert code == 0, shown
    review = (date.today() + timedelta(days=90)).isoformat()
    reason = ("--reason", "Check the move to TypeScript 7", "--review-by", review)
    upgraded = installed.cli(
        project.clone, "upgrade", f"tsc={OPT_IN}", *reason, env=project.env
    )
    assert upgraded["code"] == "upgraded"
    assert f'[exceptions.tsc]\nversion = "{OPT_IN}"\n' in (
        (project.clone / "gateseal.toml").read_text()
    )
    project.commit()
    code, shown = project.push()
    assert code != 0, shown
    assert (
        "tsconfig.json(8,5): error TS5102: Option 'baseUrl' has been removed."
    ) in shown
    assert project.pushed() == project.git("rev-parse", "HEAD~")


def test_the_gate_reports_the_install_and_tsc_on_the_policy_s_node(installed, project):
    gated = installed.cli(project.clone, "gate", env=project.env)
    assert gated["code"] == "checks_passed"
    shared = {"severity": "info", "category": "hook", "origin": "built-in"}
    shared |= {"stage": "ci", "status": "passed", "modified": False, "node": NODE}
    hooks = [d for d in gated["diagnostics"] if d["category"] == "hook"]
    assert hooks == [
        shared
        | {
            "tool": "gateseal",
            "hook": "gateseal-node-packages",
            "message": f"{INSTALL}: Passed",
        },
        shared | {"tool": "tsc", "hook": "tsc", "message": "tsc: Passed"},
    ]
    assert project.installed("zod") == ZOD
    healthy = installed.cli(project.clone, "status", env=project.env)
    assert healthy["code"] == "healthy"
    assert NOTE in [d["message"] for d in healthy["diagnostics"]]


def test_pre_commit_runs_the_checks_of_a_push_without_one(project):
    (project.clone / "src/total.ts").write_text(WRONG_TOTAL)
    pre_commit = Path(project.env["HOME"], ".local/share/gateseal/tools/pre-commit")
    arguments = ["run", "--hook-stage", "pre-push", "--all-files"]
    ran = process(
        [pre_commit / "bin/pre-commit", *arguments], project.clone, project.env
    )
    shown = ran.stdout + ran.stderr
    assert ran.returncode == 1, shown
    assert (status(INSTALL, shown), status("tsc", shown)) == ("Passed", "Failed")
    assert WRONG_TOTAL_ERROR in shown
    assert project.installed("zod") == ZOD


@pytest.mark.parametrize(
    ("lockfiles", "code", "message", "step"),
    [
        (
            (),
            "missing_project",
            "has neither package-lock.json nor pnpm-lock.yaml. Only npm and pnpm "
            "projects are supported.",
            "Run npm install or pnpm install and commit the lockfile",
        ),
        (
            ("package-lock.json", "pnpm-lock.yaml"),
            "ambiguous_project",
            "has both package-lock.json and pnpm-lock.yaml, so its package manager "
            "is not clear.",
            "Delete the lockfile that the project does not use and commit the change",
        ),
        (
            ("package-lock.json", "npm-shrinkwrap.json"),
            "ambiguous_project",
            "has both package-lock.json and npm-shrinkwrap.json, and npm reads "
            "npm-shrinkwrap.json in place of package-lock.json.",
            "Delete npm-shrinkwrap.json and commit the change",
        ),
    ],
    ids=["neither lockfile", "both lockfiles", "npm shrinkwrap"],
)
def test_configure_needs_exactly_one_lockfile(
    installed, consumer, lockfiles, code, message, step
):
    write_package(consumer, NPM, lockfile=False)
    locks = {manager.lockfile: manager.locks[PINNED] for manager in MANAGERS}
    locks["npm-shrinkwrap.json"] = NPM.locks[PINNED]
    for name in lockfiles:
        (consumer / name).write_text(locks[name])
    refused = installed.cli(consumer, "configure", *ADOPT, code=2)
    assert refused["code"] == code
    assert refused["diagnostics"][0]["message"] == (
        f"tsc runs in the repository's Node project, but the repository {message}"
    )
    assert refused["next_steps"] == [
        f"{step}, or stop selecting typescript-types in gateseal.toml. Then run "
        "gateseal configure."
    ]
    assert not (consumer / "gateseal.toml").exists()


def with_typecheck(data):
    """Offer the Team-defined typecheck, at the bundled tsc's pin."""
    (data / "tools").mkdir()
    (data / "tools/typecheck.toml").write_text(TYPECHECK)
    policy = data / "policy.toml"
    policy.write_text(policy.read_text() + TEAM_TYPES)
    lock = json.loads((data / "policy.lock").read_bytes())
    record = lock["components"]["tsc"]["versions"][TYPESCRIPT]
    lock["components"]["typecheck"] = {"versions": {TYPESCRIPT: record}}
    (data / "policy.lock").write_bytes(canonical(lock))


LOOSE = TSCONFIG.replace('"strict": true', '"strict": false')
"""A tsconfig.json that turns the strict checks off."""
UNTYPED = "export const double = (value) => value * 2;\n"
UNTYPED_ERROR = (
    "src/double.ts(1,24): error TS7006: Parameter 'value' implicitly has an 'any' type."
)


def test_a_team_tool_s_strict_flag_wins_over_a_loose_tsconfig_at_each_commit(
    installed, tmp_path
):
    """The policy's arguments follow --project, so tsconfig.json cannot weaken them."""
    origin = tmp_path / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", with_typecheck)
    env = environment(installed, origin)
    consumer = write_project(clone(tmp_path / "origin", tmp_path / "consumer"), NPM)
    adopt = ("--capability", "team-types", "--policy", URL, "--policy-path")
    adopt += ("policy", "--policy-tag", "v1")
    installed.cli(consumer, "configure", *adopt, env=env)
    assert LOOSE != TSCONFIG
    (consumer / "tsconfig.json").write_text(LOOSE)
    (consumer / "src/double.ts").write_text(UNTYPED)
    success(["git", "add", "-A"], consumer, env)
    refused = process(["git", "commit", "-m", "Adopt"], consumer, env)
    shown = refused.stdout + refused.stderr
    assert refused.returncode == 1, shown
    assert (status(INSTALL, shown), status("typecheck", shown)) == ("Passed", "Failed")
    assert UNTYPED_ERROR in shown
    (consumer / "src/double.ts").write_text(
        UNTYPED.replace("(value)", "(value: number)")
    )
    success(["git", "add", "-A"], consumer, env)
    committed = process(["git", "commit", "-m", "Adopt"], consumer, env)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == 0, shown
    assert status("typecheck", shown) == "Passed", shown
