"""The package managers of a Node project, and which one a repository uses.

A Node project is a package.json and one package manager's lockfile at the
repository root. The project check at configure and the hook that installs
the project's packages both read these records, so they agree on what a
project is and how to install it. Nothing here starts a process.
"""

from dataclasses import dataclass
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.tools.node import Package

PROJECT_FILE = "package.json"


@dataclass(frozen=True)
class PackageManager:
    """One package manager: its lockfile and how it installs what that pins.

    package is the npm package that provides the executable when Node's
    distribution does not. checks holds argument lists of the package manager's
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
    package: Package | None
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
        (
            "ci",
            "--dry-run",
            "--no-global",
            "--location=project",
            "--no-usage",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ),
        (
            "ls",
            "--package-lock-only",
            "--package-lock",
            "--no-global",
            "--location=project",
            "--include=dev",
            "--include=optional",
            "--include=peer",
            "--no-usage",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ),
    ),
    (
        "install",
        "--no-save",
        "--package-lock",
        "--no-package-lock-only",
        "--no-dry-run",
        "--no-global",
        "--location=project",
        "--include=dev",
        "--include=optional",
        "--include=peer",
        "--no-usage",
        "--ignore-scripts",
        "--no-audit",
        "--no-fund",
    ),
    "node_modules/.package-lock.json",
    ("npm-shrinkwrap.json",),
    (("npm_config_git", "git"), ("npm_config_node_options", "null")),
)
"""npm, which Node's distribution includes.

npm ci deletes node_modules on each run, so its dry run only proves that the
lockfile matches package.json, and fails when it does not. It compares only
versions, so npm ls --package-lock-only then fails when a dependency in
package.json comes from another source than the lockfile names, such as
another tarball of the same version. npm install --no-save then installs only
what changed, and leaves the lockfile as it is.
Options on the command line win over an .npmrc and the environment, so npm
installs every package that the lockfile pins, development dependencies
included, and works on the project rather than the global folder of Node. The
dry run also rewrites npm's record of node_modules to what the lockfile pins,
though it installs nothing. npm install would trust that record and keep a
stale package, so the hook puts the record back as it was. npm install then
replaces each package whose source changed, also at the same version, such as
a tarball that the lockfile names anew.

For a git dependency with a script, npm starts npm again in a clone of the
dependency, where the clone's .npmrc applies. That npm gets npm's settings only
from the environment, which wins over every .npmrc. So the environment takes git
from PATH and gives Node no options, and no .npmrc names a program or code to
run.
"""

PNPM = PackageManager(
    "pnpm",
    "pnpm-lock.yaml",
    Package("pnpm", "10.28.0"),
    (),
    (
        "install",
        "--frozen-lockfile",
        "--ignore-scripts",
        "--ignore-pnpmfile",
        "--no-use-store-server",
        "--no-prod",
        "--no-dev",
        "--no-only",
        "--optional",
        "--lockfile",
        "--no-lockfile-only",
        "--lockfile-dir=.",
        "--config.git-branch-lockfile=false",
        "--config.merge-git-branch-lockfiles=false",
        "--config.ignore-package-manifest=false",
        "--config.resolution-only=false",
        "--config.fix-lockfile=false",
        "--config.dedupe=false",
        "--config.update=false",
        "--modules-dir=node_modules",
        "--package-import-method=auto",
        "--config.enable-modules-dir=true",
        "--config.confirm-modules-purge=false",
        "--config.symlink=true",
        "--config.node-version=null",
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
lockfile no longer matches package.json. Options on the command line win over
an .npmrc, pnpm-workspace.yaml and the environment, so pnpm installs every
package that the lockfile pins, development dependencies included, and runs no
pnpmfile. pnpm reads pnpm-lock.yaml alone, and no lockfile of a git branch.
pnpm-workspace.yaml can set any option of pnpm install, so the options also
turn off those that skip the check of package.json, rewrite the lockfile,
update packages, or resolve without an install.
When node_modules holds another install, such as one without development
dependencies, pnpm recreates it without a question, which it cannot ask without
a terminal. pnpm install has no option for some settings, so --config sets
them, and null restores a setting's default.
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
