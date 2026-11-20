"""Repository Hooks: the Manifest's [hooks] table of scripts in the repository.

A Repository Maintainer adds a hook that runs a file of the repository. It runs
after every policy hook, at a commit, in gateseal run and in the CI gate, so a
commit and CI keep running one generated configuration. Its stages can move it
to a push, or to the gate alone. It only adds a check: its ID may name no hook
of the policy, and gateseal installs nothing for it.
"""

import json
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from gateseal.compiler_release import OWN_HOOKS
from gateseal.diagnostics import GatesealError, Note
from gateseal.managed_files import MANIFEST
from gateseal.paths import resolved
from gateseal.tools.model import (
    AT_COMMIT,
    NAME,
    STAGE_SETS,
    Hook,
    Origin,
    OutputFormat,
    Scope,
    Stage,
    stage_set,
)
from gateseal.validation import mapping, sequence, string

FIELDS = frozenset({"name", "run", "args", "files", "stages"})

RUN = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
"""A file's path from the repository root, in portable characters only, so the
generated entry needs no quoting."""


@dataclass(frozen=True)
class RepositoryHook:
    """One [hooks.<id>] table.

    run is the file it starts, relative to the repository root, with args
    before the file names. files is pre-commit's pattern of the files that
    start it, or None for every file. stages is one of STAGE_SETS' sets.
    """

    id: str
    name: str
    run: str
    args: tuple[str, ...]
    files: str | None
    stages: frozenset[Stage]

    @property
    def hook(self) -> Hook:
        """Return the hook as results name it: origin repository, tool its file."""
        return Hook(
            self.run,
            Origin.REPOSITORY,
            self.id,
            self.name,
            self.args,
            (),
            OutputFormat.PLAIN,
        )

    def note(self) -> Note:
        files = "every file" if self.files is None else f"files matching {self.files}"
        command = shlex.join([self.run, *self.args])
        return Note(
            "info",
            "repository_hook",
            f"Repository hook {self.id}: {command}, on {files}.",
        )


def parse_hooks(value: object) -> tuple[RepositoryHook, ...]:
    """Validate the Manifest's [hooks] table, keeping its order."""
    return tuple(
        _hook(hook_id, table) for hook_id, table in mapping(value, "hooks").items()
    )


def _hook(hook_id: str, value: object) -> RepositoryHook:
    field = f"hooks.{hook_id}"
    table = mapping(value, field)
    extra = sorted(set(table) - FIELDS)
    if extra:
        raise GatesealError(
            "invalid_field",
            f"{field} has {', '.join(extra)}, which a repository hook does not take. "
            "It takes name, run, args, files and stages.",
        )
    missing = sorted({"name", "run"} - set(table))
    if missing:
        raise GatesealError("invalid_field", f"{field} needs {' and '.join(missing)}.")
    if not NAME.fullmatch(hook_id):
        raise GatesealError(
            "invalid_field",
            f"{field} needs an ID of lowercase letters, digits and -.",
        )
    run = string(table["run"], f"{field}.run")
    if not RUN.fullmatch(run) or {".", ".."} & set(run.split("/")):
        raise GatesealError(
            "invalid_field",
            f"{field}.run is {run!r}, but a repository hook runs a file of the "
            "repository, named by its path from the repository root, such as "
            "scripts/check.sh.",
        )
    return RepositoryHook(
        hook_id,
        string(table["name"], f"{field}.name"),
        run,
        tuple(
            string(item, f"{field}.args")
            for item in sequence(table.get("args", []), f"{field}.args")
        ),
        _files(table["files"], f"{field}.files") if "files" in table else None,
        _stages(table["stages"], f"{field}.stages") if "stages" in table else AT_COMMIT,
    )


def _files(value: object, field: str) -> str:
    pattern = string(value, field)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise GatesealError(
            "invalid_field", f"{field} is not a regular expression: {exc}."
        ) from None
    return pattern


def _stages(value: object, field: str) -> frozenset[Stage]:
    listed = [string(item, field) for item in sequence(value, field)]
    stages = stage_set(listed)
    if stages is None:
        accepted = [
            json.dumps([stage for stage in Stage if stage in stages])
            for stages in STAGE_SETS
        ]
        raise GatesealError(
            "invalid_field",
            f"{field} must be {', '.join(accepted[:-1])} or {accepted[-1]}.",
        )
    return stages


def require_own_ids(
    hooks: tuple[RepositoryHook, ...], policy_hooks: Iterable[Hook]
) -> None:
    """Require no repository hook to share an ID or a name with another hook.

    The IDs and names of policy_hooks and of gateseal's own hooks are taken,
    because results and pre-commit's SKIP name a hook by its ID and
    pre-commit's report by its name.
    """
    ids = dict.fromkeys(OWN_HOOKS, "gateseal's own hook")
    names = dict.fromkeys(OWN_HOOKS.values(), "gateseal's own hook")
    for hook in policy_hooks:
        ids[hook.id] = names[hook.name] = "a hook of the selected policy"
    for hook in hooks:
        for kind, value, taken in (("ID", hook.id, ids), ("name", hook.name, names)):
            if value in taken:
                raise GatesealError(
                    "duplicate_identifier",
                    f"hooks.{hook.id} has the {kind} {value}, which {taken[value]} "
                    "has.",
                    next_step=f"Give hooks.{hook.id} another {kind} in {MANIFEST}, "
                    "then run the command again.",
                )
        ids[hook.id] = names[hook.name] = f"hooks.{hook.id}"


def require_files(root: Path, hooks: tuple[RepositoryHook, ...]) -> None:
    """Require each hook's run to name a file in the repository.

    A link may point to another file of the repository, never out of it.
    """
    top = resolved(root)
    for hook in hooks:
        target = resolved(root / hook.run)
        if not target.is_file() or not target.is_relative_to(top):
            raise GatesealError(
                "invalid_field",
                f"hooks.{hook.id}.run names {hook.run}, which is not a file in the "
                "repository.",
                next_step=f"Add {hook.run}, or name another file in "
                f"hooks.{hook.id}.run. Then run gateseal configure.",
            )


def repository_entry(hooks: tuple[RepositoryHook, ...]) -> dict[str, object]:
    """Render hooks as the native configuration's last repository.

    pre-commit starts run from the repository root with args and the file
    names, through the file's own interpreter line, in each of its stages.
    """
    return {
        "repo": "local",
        "hooks": [
            hook.hook.base_entry(None, hook.stages, Scope.FILES)
            | {
                "language": "unsupported_script",
                "entry": shlex.join([hook.run, *hook.args]),
            }
            | ({} if hook.files is None else {"files": hook.files})
            for hook in hooks
        ],
    }
