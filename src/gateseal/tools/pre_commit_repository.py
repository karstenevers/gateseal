"""The pre-commit-repository kind: a Python-language pre-commit hook repository.

Guarantees this module owns, whatever a definition says:

- Exact pin: the Policy Lock records each reviewed version's full commit,
  which gateseal policy lock read from its release tag. The Lock records the
  selected one, and pre-commit clones exactly it.
- Version proof: when a definition names an executable, activation and
  doctor refuse any other version. Ruff's names one, because ruff-pre-commit
  installs Ruff from an index by version and without a hash. A definition
  whose hooks are the repository's own code, as pre-commit-hooks' are, names
  none, and the commit alone proves its version.
- No host fallback: every hook environment is attributed to a locked tool by
  repository and revision, every probe and recorded executable is resolved
  inside that environment, and commits refuse a missing one.
- Closed failure: every refusal names gateseal activate.

Each version's Policy Lock record is its revision. The Lock records each
tool in components under its name.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar

from gateseal.bootstrap import HookEnvironment, PreparedRecord, sha
from gateseal.diagnostics import GatesealError
from gateseal.processes import execute, find_python
from gateseal.tools.model import (
    Catalog,
    Hook,
    LockSection,
    MissingUpstream,
    Release,
    ToolDefinition,
    ToolUse,
    Upstream,
    changed_upstream,
    https_url,
    pattern,
)
from gateseal.validation import fields, string

COMMIT = re.compile("[0-9a-f]{40}")


@dataclass(frozen=True)
class PreCommitRepository:
    """The [source] of a hook repository: its URL and its release tag pattern."""

    kind: ClassVar[str] = "pre-commit-repository"
    local: ClassVar[bool] = False
    python: ClassVar[bool] = True
    repository: str
    tag: str

    def offer(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        records: Mapping[str, object],
    ) -> RepositoryTool:
        """Read each version's revision, a full lowercase commit, from its record."""
        versions: dict[str, RevisionRelease] = {}
        for version, requires in catalog.requires.items():
            field = f"components.{definition.name}.versions.{version}"
            record = fields(records[version], {"revision"}, field)
            revision = string(record["revision"], f"{field}.revision")
            if not COMMIT.fullmatch(revision):
                raise GatesealError(
                    "invalid_field", f"{field}.revision must be a full commit ID."
                )
            versions[version] = RevisionRelease(version, requires, revision)
        return RepositoryTool(definition, self, use, catalog.pin, versions, catalog.pin)

    def lock(
        self,
        definition: ToolDefinition,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Read the commit that the version's release tag names."""
        name, tag = definition.name, self.tag.replace("{version}", version)
        commit = upstream.tagged(self.repository, tag)
        if commit is None:
            raise MissingUpstream(
                "upstream_tag_missing",
                f"{self.repository} has no tag {tag} for {name} {version}.",
                name,
                version,
            )
        record: dict[str, object] = {"revision": commit}
        if recorded is not None and recorded != record:
            raise changed_upstream(
                name, version, f"tag {tag} of {self.repository}", recorded, record
            )
        return record


def parse_source(value: object, field: str) -> PreCommitRepository:
    table = fields(value, {"repository", "tag"}, field)
    return PreCommitRepository(
        https_url(table["repository"], f"{field}.repository"),
        pattern(table["tag"], f"{field}.tag", ("version",)),
    )


@dataclass(frozen=True)
class RevisionRelease(Release):
    revision: str


@dataclass(frozen=True)
class RepositoryTool:
    """A hook repository as a policy offers it, at its selected version."""

    definition: ToolDefinition
    source: PreCommitRepository
    use: ToolUse
    pin: str
    versions: Mapping[str, RevisionRelease]
    version: str

    @property
    def release(self) -> RevisionRelease:
        return self.versions[self.version]

    def select(self, version: str) -> RepositoryTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> RepositoryTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    def entry(self, exclude: str | None, python: str | None) -> dict[str, object]:
        hooks: list[object] = []
        for hook in self.use.hooks:
            entry = hook.base_entry(exclude)
            if hook.args:
                entry["args"] = list(hook.args)
            if python is not None:
                entry["language_version"] = python
            hooks.append(entry)
        return {
            "repo": self.source.repository,
            "rev": self.release.revision,
            "hooks": hooks,
        }

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the components record, whose tools name the proven executable."""
        executable = self.definition.executable
        return (
            "components",
            self.definition.name,
            {
                "repository": self.source.repository,
                "version": self.version,
                "revision": self.release.revision,
                "hook_ids": [hook.id for hook in self.use.hooks],
                "tools": {} if executable is None else {executable.name: self.version},
            },
        )


def repository_tools(tools: Iterable[object]) -> dict[str, RepositoryTool]:
    """Return the hook repositories among tools, by name."""
    return {
        tool.definition.name: tool for tool in tools if isinstance(tool, RepositoryTool)
    }


def _probe(
    tool: RepositoryTool, python: str, root: Path, env: dict[str, str]
) -> tuple[str, ...]:
    """Run each hook's probe, then prove the version, in one hook environment.

    Returns the version line the executable printed, if the tool names one.
    """
    scripts = Path(python).parent

    def run(name: str, *arguments: str) -> str:
        executable = scripts / name
        try:
            result = execute([str(executable), *arguments], root, env, stdin=b"")
        except GatesealError as exc:
            raise GatesealError(
                exc.code,
                f"Native hook executable {executable} failed its probe. {exc}",
                exc.exit_code,
                "gateseal activate",
            ) from exc
        return result.stdout.decode(errors="replace").strip()

    for hook in tool.use.hooks:
        run(*hook.probe)
    executable = tool.definition.executable
    if executable is None:
        return ()
    line = run(executable.name, *executable.version)
    executable.require_version(line, tool.version, str(scripts / executable.name))
    return (line,)


def stored_repositories(home: Path) -> dict[Path, tuple[str, str]]:
    """Return the URL and revision of each clone that home's database names.

    pre-commit records every clone in db.db as its URL, revision and path.
    """
    database = home / "db.db"
    if not database.is_file():
        return {}
    connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)
    try:
        rows = connection.execute("SELECT repo, ref, path FROM repos").fetchall()
    except sqlite3.Error as exc:
        raise GatesealError(
            "hook_preparation_failed", f"Cannot read pre-commit's store: {exc}", 4
        ) from exc
    finally:
        connection.close()
    return {
        Path(str(path)).resolve(): (str(repo), str(ref)) for repo, ref, path in rows
    }


def _base_interpreter(environment: Path) -> Path | None:
    """Return the resolved interpreter that virtualenv built environment from."""
    try:
        text = (environment / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError, UnicodeError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "base-executable":
            return Path(value.strip()).resolve()
    return None


def hook_environments(
    tools: Iterable[object],
    home: Path,
    interpreter: Path,
    uv: str,
    root: Path,
    env: dict[str, str],
) -> dict[Path, HookEnvironment]:
    """Find, attribute, and probe every hook environment home's database names.

    Each environment is attributed to the hook repository whose repository and
    revision pre-commit cloned it from, and every one needs an environment.
    Each must be built from interpreter, the resolved Hook Python or runtime
    CPython. Each record names the repository's tool and every executable its
    hooks' probes and version proof start.
    """
    repositories = repository_tools(tools)
    owners = {
        (tool.source.repository, tool.release.revision): name
        for name, tool in repositories.items()
    }
    environments: dict[Path, HookEnvironment] = {}
    for clone, source in sorted(stored_repositories(home).items()):
        for marker in sorted(clone.glob("py_env-*/.install_state_v1")):
            name = owners.get(source)
            if name is None:
                raise GatesealError(
                    "hook_preparation_failed",
                    f"pre-commit prepared {marker.parent} from no locked repository.",
                    4,
                )
            base = _base_interpreter(marker.parent)
            if base != interpreter:
                raise GatesealError(
                    "hook_preparation_failed",
                    f"pre-commit built {marker.parent} on {base}, "
                    f"not on {interpreter}.",
                    4,
                )
            python = find_python(uv, root, env, str(marker.parent))
            tool = repositories[name]
            _probe(tool, python, root, env)
            names = {hook.probe[0] for hook in tool.use.hooks}
            if tool.definition.executable is not None:
                names.add(tool.definition.executable.name)
            environments[marker.parent] = HookEnvironment(
                name,
                Path(python),
                sha(marker.read_bytes()),
                tuple(Path(python).parent / program for program in sorted(names)),
            )
    missing = set(repositories) - {e.component for e in environments.values()}
    if missing:
        raise GatesealError(
            "hook_preparation_failed",
            f"pre-commit did not prepare a {', '.join(sorted(missing))} environment.",
            4,
        )
    return environments


def probe(
    tools: Iterable[object], record: PreparedRecord, root: Path, env: dict[str, str]
) -> list[str]:
    """Probe every recorded hook environment; return the version lines, sorted."""
    repositories = repository_tools(tools)
    reported: list[str] = []
    for environment in record.hook_environments.values():
        tool = repositories.get(environment.component)
        if tool is None:
            raise GatesealError(
                "needs_activation",
                f"A hook environment belongs to {environment.component}, "
                "which the Lock does not select.",
                3,
                "gateseal activate",
            )
        reported.extend(_probe(tool, str(environment.python), root, env))
    return sorted(set(reported))
