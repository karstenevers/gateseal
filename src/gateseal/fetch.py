"""mise exec downloads a release binary when its hook starts, so a failed
download there would read as a failed check.

pre-commit builds every hook environment, also of hooks that a run skips by
stage or SKIP, so that gateseal repeats none of pre-commit's rules for which
hooks run.

uv run installs a uv project's tools with the project, and a failure there can
be the project's own, so they are not fetched.
"""

import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from time import sleep

from gateseal import lock, setup
from gateseal.diagnostics import GatesealError, Result
from gateseal.environments import tool_environment
from gateseal.managed_files import CONFIG
from gateseal.platforms import machine
from gateseal.processes import execute, pre_commit_command
from gateseal.tools.release_binary import BinaryTool, require_mise

WAITS = (1, 3)

ENVIRONMENT = re.compile(r"\[INFO\] (?:Initializing|Installing) environment for (.+)\.")

RERUN = (
    "Run the command again, or start the CI job again. If the download fails "
    "each time, read the output for the cause."
)


@dataclass(frozen=True)
class Download:
    command: list[str]
    installed: str
    failed: Callable[[str], str]


def run(locked: lock.Locked) -> Result:
    entries = fetch(locked, setup.require_pre_commit())
    return Result(
        "fetch",
        "fetched",
        "The selected release binaries and the hook environments are installed.",
        details="\n".join(str(entry["message"]) for entry in entries),
        entries=entries,
    )


def fetch(locked: lock.Locked, python: str) -> tuple[dict[str, object], ...]:
    binaries = [tool for tool in locked.selection.tools if isinstance(tool, BinaryTool)]
    downloads = [*release_binaries(binaries), hook_environments(python)]
    return download(downloads, locked.root, dict(os.environ))


def release_binaries(tools: Iterable[BinaryTool]) -> list[Download]:
    archives = [(tool, _archive_url(tool)) for tool in tools]
    if not archives:
        return []
    mise = require_mise(tool_environment())
    return [_release_binary(mise, tool, url) for tool, url in archives]


def _release_binary(mise: str, tool: BinaryTool, url: str) -> Download:
    name = _name(tool)
    return Download(
        [mise, "--no-config", "install", tool.mise_request],
        f"mise installed {name} from {url}.",
        lambda output: f"mise could not install {name} from {url}",
    )


def _name(tool: BinaryTool) -> str:
    return f"{tool.definition.name} {tool.version}"


def _archive_url(tool: BinaryTool) -> str:
    name = _name(tool)
    archives = tool.release.platforms
    platform = machine()
    if platform is None or platform not in archives:
        raise GatesealError(
            "unsupported_machine",
            f"{name} has no archive for this machine, so the code was not checked.",
            3,
            f"Run the checks on a machine that {name} has an archive for: "
            f"{', '.join(sorted(archives))}.",
        )
    return archives[platform].url


def hook_environments(python: str) -> Download:
    # ENVIRONMENT matches only output without color.
    arguments = ["install-hooks", "--color", "never", "--config", CONFIG]
    return Download(
        pre_commit_command(python, arguments),
        f"pre-commit built the hook environments of {CONFIG}.",
        _unbuilt,
    )


def _unbuilt(output: str) -> str:
    started = ENVIRONMENT.findall(output)
    environment = (
        f"the hook environment of {started[-1]}"
        if started
        else f"the hook environments of {CONFIG}"
    )
    return f"pre-commit could not build {environment}"


def download(
    downloads: Iterable[Download], root: Path, env: dict[str, str]
) -> tuple[dict[str, object], ...]:
    entries: list[dict[str, object]] = []
    for item in downloads:
        attempts = _run_until_success(item, root, env)
        retried = "" if attempts == 1 else f" The download took {attempts} attempts."
        entries.append(
            {
                "severity": "info",
                "category": "download",
                "attempts": attempts,
                "message": item.installed + retried,
            }
        )
    return tuple(entries)


def _run_until_success(item: Download, root: Path, env: dict[str, str]) -> int:
    output = ""
    for attempt, wait in enumerate((*WAITS, None), 1):
        process = execute(item.command, root, env, allow_failure=True)
        if process.returncode == 0:
            return attempt
        output = (process.stdout + process.stderr).decode("utf-8", errors="replace")
        if wait is not None:
            sleep(wait)
    raise GatesealError(
        "download_failed",
        f"{item.failed(output)} in {len(WAITS) + 1} attempts, so the code was not "
        "checked.",
        4,
        RERUN,
        details=output,
    )
