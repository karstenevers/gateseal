import json
import re
from collections.abc import Callable
from dataclasses import dataclass

ENTRY_LIMIT = 200

NEXT_STEPS = {
    "concurrent_change": (
        "Run the command again when no other program changes the file."
    ),
    "duplicate_identifier": (
        "Remove the duplicate, or give each item its own name, then run the command "
        "again."
    ),
    "file_mode_mismatch": (
        "Remove the execute permission from the file with chmod a-x, then run the "
        "command again."
    ),
    "filesystem_error": (
        "Fix the file system problem that the message names, such as a missing path or "
        "a missing permission, then run the command again."
    ),
    "incompatible_exception": (
        "Change the exceptions in gateseal.toml so that each version meets the "
        "requires of the other tools, then run gateseal configure."
    ),
    "incompatible_policy": (
        "Change the pins in policy.toml so that each pinned version meets the requires "
        "of the other tools, then run gateseal policy check."
    ),
    "interrupted": "Run the same command again.",
    "invalid_choice": (
        "Correct the choice as the message says, then run the command again."
    ),
    "invalid_exception": (
        "Correct the exception in gateseal.toml as the message says, then run gateseal "
        "configure."
    ),
    "invalid_field": (
        "Correct the field that the message names, or restore its file from Git, then "
        "run the command again."
    ),
    "invalid_invocation": (
        "Correct the arguments as the message says. Add --help to the command to see "
        "its options."
    ),
    "invalid_json": (
        "Restore the file from Git. gateseal configure writes gateseal.lock again, and "
        "gateseal policy lock writes policy.lock again."
    ),
    "invalid_policy_source": (
        "Correct the policy commit or tag in gateseal.toml, or in the --policy-commit "
        "or --policy-tag flag, then run the command again."
    ),
    "invalid_toml": (
        "Correct the TOML syntax of the file, such as a duplicate key or a missing "
        "quote, then run the command again."
    ),
    "mise_version_mismatch": (
        "Upgrade mise to the version that the message names, or a later one, then run "
        "the command again."
    ),
    "missing_capability": (
        "Run gateseal configure --capability with a capability that the policy offers, "
        "such as hygiene."
    ),
    "missing_configuration": (
        "Run gateseal configure, with --capability when the repository has no "
        "gateseal.toml yet."
    ),
    "missing_policy_asset": (
        "Add the missing file to the policy directory, or correct the name or "
        "directory that points to it, then run the command again."
    ),
    "missing_prerequisite": (
        "Install the program that the message names and put it on PATH, then run the "
        "command again."
    ),
    "operation_in_progress": (
        "Wait until the other gateseal command ends, then run this command again."
    ),
    "tool_failed": (
        "Fix the problem that the tool output in the message shows, then run the "
        "command again."
    ),
    "tool_unavailable": (
        "Make sure that Git and the other required programs are installed and can "
        "start, then run the command again."
    ),
    "unknown_component": (
        "Name a tool that the message lists, then run the command again."
    ),
    "unknown_version": (
        "Name a version that the message lists, then run the command again."
    ),
    "unsafe_path": (
        "Use a relative path inside the repository or policy that names a regular file "
        "or directory, not a symbolic link, then run the command again."
    ),
    "unsafe_policy_entry": (
        "Replace the entry with a regular, non-executable file in the policy "
        "repository, then release the policy again."
    ),
    "unsupported_capability": (
        "Select one or more capabilities that the policy offers, each once, then run "
        "gateseal configure."
    ),
    "unsupported_choice": (
        "Remove the choice, or name one that the policy offers, then run the command "
        "again."
    ),
    "unsupported_component": (
        "Name only components that the policy offers and the selected capabilities "
        "use, then run the command again."
    ),
    "unsupported_hook": (
        "Name only hooks that the tools of the capability run, then run the command "
        "again."
    ),
    "unsupported_path": (
        "Move the repository to a path without a line break, then run the command "
        "again."
    ),
    "unsupported_resource": (
        "Install gateseal again with uv tool install gateseal, then run the command "
        "again."
    ),
    "unsupported_schema": (
        "Set schema to the version that the message names, or use the newer gateseal "
        "that wrote the file."
    ),
    "unsupported_source": (
        'Set policy.source in gateseal.toml to "bundled" or "git", then run gateseal '
        "configure."
    ),
    "unused_choice": (
        "Remove the choice from gateseal.toml, or select its capability, then run "
        "gateseal configure."
    ),
    "unused_exception": (
        "Remove the exception from gateseal.toml, then run gateseal configure."
    ),
    "uv_version_mismatch": (
        "Upgrade uv to the version that the message names, or a later one, then run "
        "the command again."
    ),
}
"""The default next step of each error code, for a failure that gives none."""


class GatesealError(Exception):
    def __init__(
        self, code: str, message: str, exit_code: int = 2, next_step: str = ""
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.next_step = next_step or NEXT_STEPS.get(code, "")


def redact(text: str) -> str:
    text = re.sub(r"(?:https?|ssh)://[^\s<>'\"]+", _safe_url, text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)[^\r\n]+", r"\1[redacted]", text)
    return re.sub(
        r"\bgh[pousr]_[A-Za-z0-9._-]{36,}|"
        r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b",
        "[redacted]",
        text,
    )


def _safe_url(match: re.Match[str]) -> str:
    from urllib.parse import urlsplit, urlunsplit

    try:
        parts = urlsplit(match[0])
        host = parts.hostname or "[redacted]"
        if parts.port:
            host = f"{host}:{parts.port}"
        # An https username can be a token; an ssh one names the login.
        if parts.scheme == "ssh" and parts.username and parts.password is None:
            host = f"{parts.username}@{host}"
        return urlunsplit(
            (parts.scheme, host, parts.path, "[redacted]" if parts.query else "", "")
        )
    except ValueError:
        return "[redacted-url]"


@dataclass(frozen=True)
class Note:
    """One diagnostic with its own severity, such as a Version Exception."""

    severity: str
    category: str
    message: str


@dataclass(frozen=True)
class Result:
    command: str
    code: str = "ok"
    message: str = ""
    exit_code: int = 0
    changed: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    details: str = ""
    notes: tuple[Note, ...] = ()
    entries: tuple[dict[str, object], ...] = ()
    """Structured diagnostics, such as one per hook and one per finding."""

    def render(
        self,
        as_json: bool,
        full: bool = False,
        *,
        annotation: Callable[[str], str] | None = None,
    ) -> str:
        """Return the result as text or JSON.

        annotation turns each warning of text output into the command that a
        CI provider shows as an annotation of the run.
        """
        detail = redact(self.details)
        if not full and len(detail) > 6000:
            detail = (
                detail[:6000]
                + "\n[truncated; repeat with --verbose for complete redacted output]"
            )
        messages = [redact(s) for s in (self.message, detail) if s]
        notes = [Note(n.severity, n.category, redact(n.message)) for n in self.notes]
        if not as_json:
            lines = [
                (annotation or "Warning: {}".format)(n.message)
                if n.severity == "warning"
                else n.message
                for n in notes
            ]
            return "\n".join(
                [*messages, *lines, *(f"Next: {s}" for s in self.next_steps)]
            )
        return json.dumps(
            {
                "schema": 1,
                "command": self.command,
                "status": self._status(),
                "code": self.code,
                "changed": self.changed,
                "diagnostics": [
                    {
                        "severity": "error" if self.exit_code else "info",
                        "category": self.code,
                        "message": s,
                    }
                    for s in messages
                ]
                + [
                    {
                        "severity": n.severity,
                        "category": n.category,
                        "message": n.message,
                    }
                    for n in notes
                ]
                + self._entries(full),
                "next_steps": self.next_steps,
            },
            ensure_ascii=False,
        )

    def _status(self) -> str:
        if self.exit_code:
            return "failed"
        return "changed" if self.changed else "ok"

    def _entries(self, full: bool) -> list[dict[str, object]]:
        shown = self.entries if full else self.entries[:ENTRY_LIMIT]
        entries = [
            {k: redact(v) if isinstance(v, str) else v for k, v in entry.items()}
            for entry in shown
        ]
        if len(shown) < len(self.entries):
            entries.append(
                {
                    "severity": "info",
                    "category": "truncated",
                    "message": f"{len(self.entries) - len(shown)} more diagnostics; "
                    "repeat with --verbose for all of them.",
                }
            )
        return entries
