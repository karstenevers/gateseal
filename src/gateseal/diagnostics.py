import json
import re
from dataclasses import dataclass

ENTRY_LIMIT = 200


class GatesealError(Exception):
    def __init__(
        self, code: str, message: str, exit_code: int = 2, next_step: str = ""
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.next_step = next_step


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

    def render(self, as_json: bool, full: bool = False) -> str:
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
                ("Warning: " if n.severity == "warning" else "") + n.message
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
