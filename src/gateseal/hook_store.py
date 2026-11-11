"""Hook environments that every attempt selecting the same hooks shares.

pre-commit writes a commit's unstaged-edit patches into its home, and cleanup
reads them from the attempt it deletes, so each attempt keeps a pre-commit
home of its own. Its database names each hook repository's clone in an entry
of the interpreter store, where pre-commit finds the environment installed and
writes nothing.

An entry is the pre-commit home that pre-commit install-hooks builds for a
configuration of one hook repository. Its name is the SHA-256 of its recipe:
everything that decides what pre-commit builds and which interpreter the
environment links to. pre-commit names each clone at random and records the
attempt that built the environment, so a rebuild cannot reproduce an entry's
digests, and an attempt that recorded the old ones stops its commits until it
activates again.
"""

import contextlib
import platform
import sqlite3
from pathlib import Path
from typing import cast

from gateseal import bootstrap, interpreter_store
from gateseal.bootstrap import HookEnvironment
from gateseal.compiler_release import load_runtime
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import write_atomic
from gateseal.manifest import Selection
from gateseal.processes import execute, pre_commit_command
from gateseal.tools import pre_commit_repository
from gateseal.validation import canonical

NOT_ENVIRONMENT = frozenset({"name", "types_or", "exclude", "args"})
"""Configuration keys of a hook that never change the environment it needs.

Every other key stays in the recipe, so a key that a later rendering adds
splits entries rather than letting two hooks share a wrong environment.
"""


def recipe(
    tool: pre_commit_repository.RepositoryTool, python: str | None, interpreter: Path
) -> dict[str, object]:
    """Return what tool's entry is built from, which names the entry.

    python is the hooks' language_version, and interpreter the CPython that
    names, which the environment links to.
    """
    entry = tool.entry(None, python)
    hooks = [
        {key: value for key, value in hook.items() if key not in NOT_ENVIRONMENT}
        for hook in cast(list[dict[str, object]], entry["hooks"])
    ]
    return {
        "platform": platform.system() + "-" + platform.machine(),
        "pre_commit": bootstrap.sha(load_runtime().requirements()),
        "interpreter": str(interpreter),
        "repository": {"repo": entry["repo"], "rev": entry["rev"], "hooks": hooks},
    }


def prepare(
    selection: Selection,
    attempt: Path,
    paths: bootstrap.PreparedPaths,
    interpreters: tuple[interpreter_store.Interpreter, ...],
    uv: str,
    root: Path,
    environment: dict[str, str],
    hook_env: dict[str, str],
) -> tuple[dict[Path, bootstrap.TreeDigests], dict[Path, HookEnvironment]]:
    """Give attempt every hook environment from a sealed entry it links to.

    A missing or damaged entry is built under the store lock, which pre-commit
    and its installers inherit. The attempt's pre-commit then installs the
    worktree's configuration from its own home, which proves that home names
    every environment. interpreters are the runtime's CPython and, last, the
    one the hooks run on. environment is uv's and hook_env pre-commit's.
    Returns each entry's digests and the attempt's hook environments.
    """
    python = str(paths.pre_commit_python)
    hooks = None if selection.python is None else selection.python.name
    base = interpreters[-1].executable
    roots = tuple(interpreter.root for interpreter in interpreters)
    entries: dict[Path, bootstrap.TreeDigests] = {}
    rows: list[tuple[str, str, str]] = []
    for tool in pre_commit_repository.repository_tools(selection.tools).values():
        document = recipe(tool, hooks, base)
        name = bootstrap.sha(canonical(document))
        config = attempt / f"hooks-{name}.json"
        write_atomic(config, canonical({"repos": [document["repository"]]}))

        def build(entry: Path, lock: int, config: Path = config) -> None:
            # pytestmut: off[stmt.call_remove] -- pre-commit makes PRE_COMMIT_HOME too
            entry.mkdir(mode=0o700)  # pytestmut: skip[num.shift] -- normalize resets it
            # pytestmut: on
            execute(
                pre_commit_command(python, ["install-hooks", "--config", str(config)]),
                root,
                {**hook_env, "PRE_COMMIT_HOME": str(entry)},
                inherit=(lock,),
            )

        with interpreter_store.sealed(
            name,
            {"recipe": document},
            attempt,
            build,
            roots,
            reproducible=False,
        ) as (entry, digests):
            clones = pre_commit_repository.stored_repositories(entry)
        if len(clones) != 1:
            raise GatesealError(
                "hook_preparation_failed",
                f"The hook store entry {entry} holds {len(clones)} clones, not one.",
                4,
            )
        rows.extend((repo, ref, str(path)) for path, (repo, ref) in clones.items())
        entries[entry] = digests
    home = paths.pre_commit_home
    home.mkdir(mode=0o700, exist_ok=True)
    with contextlib.closing(sqlite3.connect(home / "db.db")) as database, database:
        database.execute(
            "CREATE TABLE IF NOT EXISTS repos (repo TEXT NOT NULL, "
            "ref TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY (repo, ref))"
        )
        database.executemany("INSERT INTO repos VALUES (?, ?, ?)", rows)
    execute(
        pre_commit_command(python, ["install-hooks", "--config", bootstrap.CONFIG]),
        root,
        hook_env,
    )
    environments = pre_commit_repository.hook_environments(
        selection.tools, home, base, uv, root, environment
    )
    for path in environments:
        if not any(path.is_relative_to(entry) for entry in entries):
            raise GatesealError(
                "hook_preparation_failed",
                f"pre-commit prepared {path} outside the interpreter store.",
                4,
            )
    return entries, environments
