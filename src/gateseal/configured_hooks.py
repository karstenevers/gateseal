"""Refuse pre-commit hooks that Git runs from configuration, outside gateseal."""

import shlex
from dataclasses import dataclass
from pathlib import Path

from gateseal.diagnostics import GatesealError
from gateseal.processes import execute


@dataclass(frozen=True)
class ConfiguredHook:
    """One enabled hook.<name> that Git runs for pre-commit, from scope's config."""

    scope: str
    name: str


DISABLED = "disabled\t"
"""What Git writes before a disabled hook's name, and a hook's own name can hold."""


def parse(listing: bytes) -> tuple[ConfiguredHook, ...]:
    """Return the enabled hooks in git hook list -z --show-scope output."""
    hooks: list[ConfiguredHook] = []
    for entry in listing.decode("utf-8", errors="replace").split("\0"):
        scope, tab, rest = entry.partition("\t")
        # The hooks directory's file is listed as translated text with no
        # scope; require_owned_hook checks that file's bytes itself.
        if not tab or rest.startswith(DISABLED):
            continue
        hooks.append(ConfiguredHook(scope, rest))
    return tuple(hooks)


def _git_output(git: str, root: Path, env: dict[str, str], *arguments: str) -> bytes:
    """Return what git prints for arguments; exit 1 is Git finding nothing."""
    listed = execute([git, *arguments], root, env, allow_failure=True)
    if listed.returncode not in (0, 1):
        detail = listed.stderr.decode("utf-8", errors="replace").strip()
        raise GatesealError(
            "git_configuration_failed",
            f"Could not read Git's hook configuration: {detail}",
            4,
        )
    return listed.stdout


def _require_untabbed(git: str, root: Path, env: dict[str, str]) -> None:
    """Refuse a hook name with a tab, which git hook list prints like DISABLED.

    An enabled hook named disabled, a tab and lint is listed exactly as a
    disabled lint, so no listing can tell the two apart. Names are compared as
    bytes, since a valid hook name need not be UTF-8.
    """
    keys = _git_output(git, root, env, "config", "-z", "--name-only", "--list")
    tabbed = sorted(
        {
            key.rsplit(b".", 1)[0][len(b"hook.") :].decode("utf-8", errors="replace")
            for key in keys.split(b"\0")
            if key.startswith(b"hook.") and b"\t" in key
        }
    )
    if tabbed:
        raise GatesealError(
            "configured_hooks",
            "Git's hook list cannot show whether these configured hooks run on "
            f"commits, because each name holds a tab: {', '.join(tabbed)}.",
            next_step=(
                f"Change your working directory to {root}. git config "
                "--show-scope --show-origin --name-only --list names the file "
                "that sets each one. Rename or remove its hook.<name> section "
                "there, then run gateseal activate."
            ),
        )


def require_no_pre_commit_hook(git: str, root: Path, env: dict[str, str]) -> None:
    """Refuse a worktree where Git runs a configured pre-commit hook."""
    listing = _git_output(
        git, root, env, "hook", "list", "-z", "--show-scope", "pre-commit"
    )
    if f"\t{DISABLED}".encode() in listing:
        _require_untabbed(git, root, env)
    hooks = parse(listing)
    if not hooks:
        return
    named = ", ".join(f"{hook.name} ({hook.scope})" for hook in hooks)
    commands = " && ".join(
        "git config --local --replace-all "
        + shlex.quote(f"hook.{hook.name}.enabled")
        + " false"
        for hook in hooks
    )
    raise GatesealError(
        "configured_hooks",
        "Git runs configuration-based pre-commit hooks before the gateseal hook "
        f"on every commit: {named}. gateseal does not manage them, so commits "
        "can run them outside gateseal's checks.",
        next_step=(
            f"Change your working directory to {root}. Turn each hook off for "
            f"this repository with {commands}, then run gateseal activate. If a "
            "hook is still listed, git config --show-scope --get-all "
            "hook.<name>.enabled names the scope that enables it; set it to "
            "false there or remove that override."
        ),
    )
