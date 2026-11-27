"""The node-project kind: a tool from npm that reads the repository's Node project.

A type checker such as tsc resolves imports from the packages in the
project's node_modules, so it cannot check without them. A tool of this kind
is an npm-package tool in every other way: each hook is one command on the
policy's Node, with the same policy facts and records.

Guarantees this module owns, whatever a definition says:

- The npm-package kind's guarantees: exact pin, exact Node, no other source,
  no install scripts and no generated file. The tool runs from gateseal's own
  install, so a version of the package that the project's lockfile pins does
  not run in its place.
- The project's packages: gateseal's own hook installs what the repository's
  lockfile pins into node_modules, without install scripts, before the tool's
  hooks run, in each stage that runs one of them. The install stops when the
  lockfile no longer matches package.json.
- A project: configure and every command that loads the Lock require
  package.json and exactly one of package-lock.json and pnpm-lock.yaml at the
  repository root. A push through pre-commit alone has no such check, but the
  install hook then stops and names what the project lacks.

A node-project tool's ESLint rule file may also name a preset whose rules
need type information. The definition's loader must give the types from the
project's tsconfig.json and node_modules, as the loader of eslint-typed does.

The first use needs the network, for Node, the tool and the project's
packages. Each version's Policy Lock record and the Lock's record are an
npm-package tool's.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from gateseal.compiler_release import NODE_PACKAGES_HOOK, NODE_PACKAGES_HOOK_NAME
from gateseal.managed_files import MANIFEST
from gateseal.tools import npm_package
from gateseal.tools.model import HOOK_STAGES, Hook, Origin, OutputFormat, Stage, Tool
from gateseal.tools.npm_package import NpmPackage, NpmTool
from gateseal.tools.package_managers import package_manager

INSTALL = Hook(
    "gateseal",
    Origin.BUILT_IN,
    NODE_PACKAGES_HOOK,
    NODE_PACKAGES_HOOK_NAME,
    (),
    (),
    OutputFormat.PLAIN,
)
"""gateseal's hook that installs the project's packages, as results name it.

It is no hook of a selection, but results report it beside them in each stage
that runs it. A failed install leaves only its output, as a plain hook does.
"""


@dataclass(frozen=True)
class NodeProject(NpmPackage):
    """The [source] of a tool that reads the Node project: its npm package."""

    kind: ClassVar[str] = "node-project"
    remark: ClassVar[str] = "; the repository's lockfile pins what it imports"
    scans_history: ClassVar[bool] = False
    typed: ClassVar[bool] = True


def parse_source(value: object, field: str) -> NodeProject:
    return NodeProject(npm_package.parse_source(value, field).package)


def _project_tools(tools: Iterable[Tool]) -> list[NpmTool]:
    return [
        tool
        for tool in tools
        if isinstance(tool, NpmTool) and isinstance(tool.source, NodeProject)
    ]


def require_project(root: Path, tools: Iterable[Tool]) -> None:
    """Require the repository at root to be a Node project for such a tool."""
    for tool in _project_tools(tools):
        package_manager(
            root,
            needs=f"{tool.definition.name} runs in the repository's Node project, but",
            instead=f", or stop selecting {tool.use.capability} in {MANIFEST}. "
            "Then run gateseal configure",
        )


def install_hook_entries(tools: Iterable[Tool]) -> tuple[dict[str, object], ...]:
    """Return gateseal's hook that installs the project's packages, if tools need it.

    It runs in each stage of a tool of this kind, and picks the package
    manager from the lockfile. Without such a tool there is no hook, so no
    other configuration changes.
    """
    found = _project_tools(tools)
    if not found:
        return ()
    stages = frozenset[Stage]().union(*(tool.definition.stages for tool in found))
    return (
        {
            "id": NODE_PACKAGES_HOOK,
            "stages": list(
                dict.fromkeys(HOOK_STAGES[stage] for stage in Stage if stage in stages)
            ),
        },
    )
