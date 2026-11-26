"""The package managers of a Node project, and which one a repository uses.

A Node project is a package.json and one package manager's lockfile at the
repository root. The project check at configure and the hook that installs
the project's packages both read these records, so they agree on what a
project is and how to install it. Nothing here starts a process.
"""

from dataclasses import dataclass
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.tools.node import Tree

PROJECT_FILE = "package.json"


@dataclass(frozen=True)
class PackageManager:
    """One package manager: its lockfile and how it installs what that pins.

    tree is npm's lockfile of the package that provides the executable when
    Node's distribution does not. checks holds argument lists of the package manager's
    own executable that prove the lockfile current and install nothing, and
    install the argument list that then installs. None of them runs code of a
    package or of the repository. record is the file in which the package
    manager notes what node_modules holds, if a check rewrites it. The hook puts
    the record back after each check, so that the install compares node_modules
    with what the last install left. lockfile_overrides holds the files that
    the package manager reads in place of its lockfile when they exist.
    environment holds settings as environment variables, which also reach a
    package manager that the package manager starts itself.
    """

    name: str
    lockfile: str
    tree: Tree | None
    checks: tuple[tuple[str, ...], ...]
    install: tuple[str, ...]
    record: str | None
    lockfile_overrides: tuple[str, ...]
    environment: tuple[tuple[str, str], ...]


NPM = PackageManager(
    "npm",
    "package-lock.json",
    None,
    (
        ("ci", "--dry-run", "--no-usage", "--ignore-scripts"),
        (
            "ls",
            "--package-lock-only",
            "--include=dev",
            "--include=optional",
            "--include=peer",
            "--no-usage",
        ),
    ),
    (
        "install",
        "--no-save",
        "--package-lock",
        "--no-package-lock-only",
        "--no-dry-run",
        "--no-usage",
        "--ignore-scripts",
        "--no-audit",
    ),
    "node_modules/.package-lock.json",
    ("npm-shrinkwrap.json",),
    (
        ("npm_config_git", "git"),
        ("npm_config_node_options", "null"),
        ("npm_config_location", "project"),
        ("npm_config_global", "false"),
    ),
)
"""npm, which Node's distribution includes.

npm ci deletes node_modules on each run, so its dry run only proves that the
lockfile matches package.json, and fails when it does not. It compares only
versions, so npm ls --package-lock-only then fails when a dependency in
package.json comes from another source than the lockfile names, such as
another tarball of the same version. npm install --no-save then installs only
what changed, and leaves the lockfile as it is. The dry run also rewrites
npm's record of node_modules to what the lockfile pins, though it installs
nothing. npm install would trust that record and keep a stale package, so the
hook puts the record back as it was. npm install then replaces each package
whose source changed, also at the same version.

The settings of the project and the user apply, as to npm install in a
terminal, and a setting can make the hook fail. The options turn off only the
settings that would make it pass with other packages than the lockfile pins:
an install that ignores the lockfile or leaves node_modules as it is, and a
check that prints its usage in place of the check or skips a kind of
dependency. npm install leaves out a kind that a setting omits, but installs a
package of that kind from package.json when a dependency of another kind needs
it, so npm ls checks every kind. No script runs, also none of the project,
which the dry run would run. npm install sends no audit request, which would
tell the registry the name and version of each package, also of a private one.

For a git dependency with a script, npm starts npm again in a clone of the
dependency, where the clone's .npmrc applies. That npm gets npm's settings only
from the environment, which wins over every .npmrc. So the environment takes git
from PATH, gives Node no options and keeps the install in the project, and no
.npmrc names a program or code to run or installs into the global prefix.
"""

PNPM = PackageManager(
    "pnpm",
    "pnpm-lock.yaml",
    Tree(
        b'{"lockfileVersion":3,"packages":{"":{"dependencies":{"pnpm":"10.28.0"}},"node_modules/pnpm":{"version":"10.28.0","resolved":"https://registry.npmjs.org/pnpm/-/pnpm-10.28.0.tgz","integrity":"sha512-Bd9x0UIfITmeBT/eVnzqNNRG+gLHZXFEG/wceVbpjjYwiJgtlARl/TRIDU2QoGaLwSNi+KqIAApk6D0LDke+SA=="}}}',
        "node_modules/pnpm/bin/pnpm.cjs",
    ),
    (),
    (
        "install",
        "--frozen-lockfile",
        "--lockfile",
        "--ignore-scripts",
        "--ignore-pnpmfile",
        "--no-use-store-server",
        "--no-lockfile-only",
        "--lockfile-dir=.",
        "--config.git-branch-lockfile=false",
        "--config.merge-git-branch-lockfiles=false",
        "--config.ignore-package-manifest=false",
        "--config.fix-lockfile=false",
        "--config.dedupe=false",
        "--config.update=false",
        "--modules-dir=node_modules",
        "--config.enable-modules-dir=true",
        "--config.symlink=true",
        "--config.filter=null",
        "--config.filter-prod=null",
    ),
    None,
    (),
    (),
)
"""pnpm, which a Node distribution lacks, so a pinned release starts it.

That release switches to the version in the project's packageManager field.
A frozen install already installs only what changed, and fails when the
lockfile no longer matches package.json. The settings of the project and the
user apply, from .npmrc, pnpm-workspace.yaml and the environment, and a setting
can make the hook fail. The options turn off only the settings that would make
it pass with other packages than pnpm-lock.yaml pins: no lockfile, a lockfile
in another folder, of a git branch or merged with one, an install without a look at
package.json or that resolves the packages anew, and an install that leaves
node_modules as it is or links no package at its top, where the packages of
another install would stay. No install script and no pnpmfile runs, and no
store server starts, because pnpm would start it without --ignore-pnpmfile.
pnpm install has no option for some settings, so --config sets them, and null
restores a setting's default.
"""

PACKAGE_MANAGERS = (NPM, PNPM)


def package_manager(root: Path, needs: str, instead: str = "") -> PackageManager:
    """Return the package manager whose lockfile is a regular file at root.

    needs starts each failure's message and says what needs the project.
    instead ends each next step with what else the user can do.
    """
    names = [manager.name for manager in PACKAGE_MANAGERS]
    lockfiles = [manager.lockfile for manager in PACKAGE_MANAGERS]
    supported = f"Only {' and '.join(names)} projects are supported."
    if not (root / PROJECT_FILE).is_file():
        raise GatesealError(
            "missing_project",
            f"{needs} the repository has no {PROJECT_FILE}. {supported}",
            next_step=f"Create the project's {PROJECT_FILE} with "
            f"{' or '.join(names)} and commit it{instead}.",
        )
    found = [m for m in PACKAGE_MANAGERS if (root / m.lockfile).is_file()]
    if not found:
        raise GatesealError(
            "missing_project",
            f"{needs} the repository has neither {' nor '.join(lockfiles)}. "
            f"{supported}",
            next_step=f"Run {' or '.join(f'{name} install' for name in names)} "
            f"and commit the lockfile{instead}.",
        )
    if len(found) > 1:
        raise GatesealError(
            "ambiguous_project",
            f"{needs} the repository has both {' and '.join(lockfiles)}, so its "
            "package manager is not clear.",
            next_step="Delete the lockfile that the project does not use and commit "
            f"the change{instead}.",
        )
    manager = found[0]
    for override in manager.lockfile_overrides:
        if (root / override).is_file():
            raise GatesealError(
                "ambiguous_project",
                f"{needs} the repository has both {manager.lockfile} and "
                f"{override}, and {manager.name} reads {override} in place of "
                f"{manager.lockfile}.",
                next_step=f"Delete {override} and commit the change{instead}.",
            )
    return manager
