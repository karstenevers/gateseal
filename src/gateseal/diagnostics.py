import json
import re
from dataclasses import dataclass
from typing import Protocol, cast

ENTRY_LIMIT = 200

COLOR = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
"""A terminal color code, or another control sequence of the same form. The gate
in CI asks pre-commit for color, and inherited settings such as PRE_COMMIT_COLOR
or FORCE_COLOR can color captured output. The form also takes a color code
whose parameters colons separate."""

COMMAND_START = re.compile(r"(##(?:vso)?)\[")
"""The start of an Azure Pipelines logging or formatting command, or of a GitHub
workflow command in the runner's legacy form, which each finds anywhere in a
line."""

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
    "missing_report": (
        "Make a hook before the reading hook write the report, in a tool whose "
        "stages include every stage of the reading hook's tool, then run the command "
        "again."
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
        self,
        code: str,
        message: str,
        exit_code: int = 2,
        next_step: str = "",
        details: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code
        self.next_step = next_step or NEXT_STEPS.get(code, "")
        self.details = details


def redact(text: str) -> str:
    """Return text without credentials, keeping the color of each clean line.

    The patterns match the text without color codes, so a code inside a token
    cannot hide it. A line that held a credential loses its color codes.
    Redaction never adds or removes a line break, so the lines pair up.
    """
    plain = COLOR.sub("", text)
    return "\n".join(
        colored if line == redacted else redacted
        for colored, line, redacted in zip(
            text.split("\n"),
            plain.split("\n"),
            _redact_plain(plain).split("\n"),
            strict=True,  # pytestmut: skip[bool.literal] -- no line is added or lost
        )
    )


def _redact_plain(text: str) -> str:
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
class Annotation:
    """One message that a CI provider shows on the run, or on a line of a file."""

    severity: str
    """error or warning."""
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None


class CiLog(Protocol):
    """The log of a CI provider's run, as gateseal's text output reaches it."""

    def annotation(self, note: Annotation, /) -> str:
        """Return the command that the provider shows as note."""
        ...

    def quote(self, text: str, /) -> str:
        """Return text that the provider prints without running a command in it."""
        ...


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
    check_times: tuple[str, ...] = ()
    """Lines of text output after the details, such as the time of each check.
    JSON leaves them out, and they are never cut."""

    def render(
        self, as_json: bool, full: bool = False, *, ci: CiLog | None = None
    ) -> str:
        """Return the result as text or JSON, which holds no color codes.

        For the log of a CI run, text output turns each warning, and each failed
        hook or history scan, into an annotation, and quotes the rest. JSON is
        one line, which Azure Pipelines and GitHub Actions would scan for a
        command, so it escapes the bracket that starts one.
        """
        detail = redact(COLOR.sub("", self.details) if as_json else self.details)
        if not full and len(detail) > 6000:
            detail = (
                detail[:6000]
                + "\n[truncated; repeat with --verbose for complete redacted output]"
            )
        messages = [redact(s) for s in (self.message, detail) if s]
        notes = [Note(n.severity, n.category, redact(n.message)) for n in self.notes]
        if not as_json:
            return self._text(messages, notes, ci)
        shown = json.dumps(
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
        return COMMAND_START.sub(r"\1\\u005b", shown)

    def summary(self) -> str:
        """Return the result as redacted Markdown for a CI provider's run summary."""
        failed = [str(owner["message"]) for owner, _ in _failures(self._redacted())]
        sections = [f"### gateseal {self.command}", redact(self.message)]
        steps = [redact(step) for step in self.next_steps]
        for heading, items in (("Failed checks:", failed), ("Next steps:", steps)):
            if items:
                sections.append(f"{heading}\n\n" + "\n".join(f"- {i}" for i in items))
        return "\n\n".join(sections) + "\n"

    def _text(self, messages: list[str], notes: list[Note], ci: CiLog | None) -> str:
        """Return the text output. In CI, one quote holds all the text, because
        GitHub prints the two lines of each quote, and the annotations follow."""
        times = [redact(line) for line in self.check_times]
        steps = [f"Next: {s}" for s in self.next_steps]
        if ci is None:
            lines = [
                f"Warning: {n.message}" if n.severity == "warning" else n.message
                for n in notes
            ]
            return "\n".join([*messages, *times, *lines, *steps])
        info = [n.message for n in notes if n.severity != "warning"]
        warnings = [
            Annotation("warning", n.message) for n in notes if n.severity == "warning"
        ]
        plain = "\n".join([*messages, *times, *info, *steps])
        return "\n".join(
            ([ci.quote(plain)] if plain else [])
            + [ci.annotation(a) for a in [*self._errors(), *warnings]]
        )

    def _errors(self) -> list[Annotation]:
        """Return an error annotation for each finding of a failed hook or history
        scan, or one for the run when the failed hook or scan has no finding."""
        groups = [
            [_finding_annotation(f) for f in findings]
            or [Annotation("error", str(owner["message"]))]
            for owner, findings in _failures(self._redacted())
        ]
        # GitHub keeps 10 error annotations of a step, so each failure gets
        # one before any failure gets a second.
        return [group[0] for group in groups] + [a for g in groups for a in g[1:]]

    def _redacted(self) -> list[dict[str, object]]:
        return [_redacted(entry) for entry in self.entries]

    def _status(self) -> str:
        if self.exit_code:
            return "failed"
        return "changed" if self.changed else "ok"

    def _entries(self, full: bool) -> list[dict[str, object]]:
        shown = self.entries if full else self.entries[:ENTRY_LIMIT]
        entries = [_redacted(entry) for entry in shown]
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


def _failures(
    entries: list[dict[str, object]],
) -> list[tuple[dict[str, object], list[dict[str, object]]]]:
    """Return each failed hook or history scan with the findings that follow it.

    A stopped hook failed too, without a finding.
    """
    groups: list[tuple[dict[str, object], list[dict[str, object]]]] = []
    for entry in entries:
        if entry["category"] in ("hook", "history"):
            groups.append((entry, []))
        elif entry["category"] == "finding":
            groups[-1][1].append(entry)
    return [
        (owner, found)
        for owner, found in groups
        if owner["status"] in ("failed", "stopped")
    ]


def _redacted(entry: dict[str, object]) -> dict[str, object]:
    return {k: redact(v) if isinstance(v, str) else v for k, v in entry.items()}


def _finding_annotation(finding: dict[str, object]) -> Annotation:
    """Return the error annotation of a finding, at its line and column.

    A notebook's finding annotates the file, and its message names the cell and
    the line in the cell. A history finding annotates the run, because its line
    is in a commit that the checkout may not hold.
    """
    message = f"{finding['tool']} {finding['rule']}: {finding['message']}"
    file, line = str(finding["file"]), cast(int, finding["line"])
    if "commit" in finding:
        return Annotation(
            "error", f"{message} at {file}:{line} in commit {finding['commit']}"
        )
    if "cell" in finding:
        return Annotation(
            "error", f"{message} (cell {finding['cell']}, line {line})", file
        )
    return Annotation("error", message, file, line, cast(int, finding["column"]))
