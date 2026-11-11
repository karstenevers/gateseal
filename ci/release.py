"""Steps of the Release workflow that need more than a shell line.

Runs under the runner's system Python, so it uses only the standard library
of Python 3.12.
"""

import argparse
import enum
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import tomllib
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import cast

HEADING = re.compile(r"## \[(?P<version>[^\]]+)\](?: - (?P<date>.*))?")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
LINK_REFERENCE = re.compile(r"\[[^\]]+\]: \S+")
INIT_VERSION = re.compile(r'^__version__ = "([^"]+)"$', re.M)
WORKFLOWS = ("tests.yml", "integration.yml")
POLL_SECONDS = 30
TESTS_DEADLINE_SECONDS = 80 * 60
INDEX_DEADLINE_SECONDS = 10 * 60
# A tuple constant keeps ruff format, which targets Python 3.14, from dropping
# the parentheses that Python 3.12 needs in an except clause.
UNREADABLE_BASE = (subprocess.CalledProcessError, tomllib.TOMLDecodeError, KeyError)


class Refusal(Exception):
    """A release cannot go on; the message names the fix."""


class Comparison(enum.Enum):
    SAME = "same"
    CHANGED = "changed"
    NO_BASE = "no base"


class RunState(enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


def project_version(pyproject: str) -> str:
    return tomllib.loads(pyproject)["project"]["version"]


def compare(head: str, base: str | None) -> Comparison:
    if base is None:
        return Comparison.NO_BASE
    return Comparison.SAME if head == base else Comparison.CHANGED


def changelog_section(changelog: str, version: str) -> str:
    """Return the body of version's dated CHANGELOG.md section, stripped."""
    lines = changelog.splitlines()
    found = [
        (index, heading)
        for index, line in enumerate(lines)
        if (heading := HEADING.fullmatch(line)) and heading["version"] == version
    ]
    if not found:
        raise Refusal(
            f"CHANGELOG.md has no section for {version}. "
            f"Add a '## [{version}] - YYYY-MM-DD' section."
        )
    index, heading = found[0]
    dated = heading["date"]
    if dated == "Unreleased":
        raise Refusal(
            f"The CHANGELOG.md section for {version} says Unreleased. "
            f"Date it: '## [{version}] - YYYY-MM-DD'."
        )
    if dated is None or not DATE.fullmatch(dated) or not valid_date(dated):
        raise Refusal(
            f"The CHANGELOG.md heading for {version} needs a date: "
            f"'## [{version}] - YYYY-MM-DD'."
        )
    body: list[str] = []
    for line in lines[index + 1 :]:
        if line.startswith("## "):
            break
        body.append(line)
    while body and (not body[-1].strip() or LINK_REFERENCE.fullmatch(body[-1])):
        body.pop()
    text = "\n".join(body).strip()
    if not text:
        raise Refusal(
            f"The CHANGELOG.md section for {version} is empty. "
            "Move the Unreleased entries into it."
        )
    return text


def valid_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def refuse_release(version: str, init: str, changelog: str, tag_exists: bool) -> None:
    found = INIT_VERSION.search(init)
    if not found or found[1] != version:
        raise Refusal(
            f"__version__ in src/gateseal/__init__.py is not {version}. "
            "Set it to the version in pyproject.toml."
        )
    changelog_section(changelog, version)
    if tag_exists:
        raise Refusal(
            f"The tag v{version} exists. Set a new version in pyproject.toml."
        )


def run_state(runs: Sequence[Mapping[str, object]]) -> RunState:
    """Classify a workflow by its newest run, the first in the list."""
    if not runs or runs[0].get("status") != "completed":
        return RunState.PENDING
    if runs[0].get("conclusion") == "success":
        return RunState.PASSED
    return RunState.FAILED


def served_wheel(document: object, version: str, sha256: str) -> str | None:
    """Return the URL of the expected wheel, or None while none is listed."""
    entries = mapping(document).get("urls")
    if not isinstance(entries, list):
        raise Refusal("The index answered without a list of files.")
    wheels = [
        mapping(entry)
        for entry in cast(list[object], entries)
        if mapping(entry).get("packagetype") == "bdist_wheel"
    ]
    if not wheels:
        return None
    expected = f"gateseal-{version}-py3-none-any.whl"
    names = [wheel.get("filename") for wheel in wheels]
    if names != [expected]:
        raise Refusal(f"The index serves {names}, not only {expected}.")
    digest = mapping(wheels[0].get("digests")).get("sha256")
    if digest != sha256:
        raise Refusal(f"The index lists {expected} with SHA-256 {digest}.")
    url = wheels[0].get("url")
    if not isinstance(url, str):
        raise Refusal(f"The index lists {expected} without a URL.")
    return url


def mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True
    ).stdout


def base_version(before: str) -> str | None:
    if not before or set(before) == {"0"}:
        return None
    try:
        return project_version(git("show", f"{before}:pyproject.toml"))
    except UNREADABLE_BASE:
        return None


def write_output(**values: str) -> None:
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.writelines(f"{key}={value}\n" for key, value in values.items())


def summarize(line: str) -> None:
    print(line)
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as summary:
            summary.write(line + "\n")


def check(_: argparse.Namespace) -> None:
    version = project_version(Path("pyproject.toml").read_text())
    if os.environ["EVENT_NAME"] == "workflow_dispatch":
        if os.environ["GITHUB_REF"] != "refs/heads/main":
            raise Refusal("Start the release on main.")
    else:
        comparison = compare(version, base_version(os.environ["BEFORE_SHA"]))
        if comparison is Comparison.NO_BASE:
            print(
                "::notice::This run cannot compare versions, so it releases "
                "nothing. Start the workflow by hand on main to release "
                f"{version}."
            )
        elif comparison is Comparison.SAME:
            summarize(f"The version stayed at {version}, so nothing is released.")
        if comparison is not Comparison.CHANGED:
            write_output(version=version, release="false")
            return
    tag = git("ls-remote", "--tags", "origin", f"refs/tags/v{version}")
    refuse_release(
        version,
        Path("src/gateseal/__init__.py").read_text(),
        Path("CHANGELOG.md").read_text(),
        bool(tag.strip()),
    )
    summarize(f"Version {version}: releasing.")
    write_output(version=version, release="true")


def fetch_json(url: str, token: str | None = None) -> object:
    headers = {"Accept": "application/json"}
    if token:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
        }
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def poll[T](attempt: Callable[[], T | None], deadline_seconds: int) -> T | None:
    deadline = time.monotonic() + deadline_seconds
    while (result := attempt()) is None and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
    return result


def wait(_: argparse.Namespace) -> None:
    api = os.environ["GITHUB_API_URL"]
    repository = os.environ["GITHUB_REPOSITORY"]
    sha = os.environ["GITHUB_SHA"]
    token = os.environ["GH_TOKEN"]

    def passed() -> bool | None:
        states: list[RunState] = []
        for workflow in WORKFLOWS:
            # Only push runs cover every runner; a manual run tests one.
            url = (
                f"{api}/repos/{repository}/actions/workflows/{workflow}/runs"
                f"?head_sha={sha}&event=push&per_page=20"
            )
            try:
                runs = mapping(fetch_json(url, token)).get("workflow_runs")
            except OSError as error:
                print(f"{url}: {error}")
                return None
            listed = [mapping(run) for run in cast(list[object], runs or [])]
            state = run_state(listed)
            print(f"{workflow}: {state.value}")
            if state is RunState.FAILED:
                newest = listed[0]
                raise Refusal(
                    f"{workflow} ended {newest['conclusion']} on {sha}: "
                    f"{newest['html_url']}"
                )
            states.append(state)
        return True if all(s is RunState.PASSED for s in states) else None

    if not poll(passed, TESTS_DEADLINE_SECONDS):
        raise Refusal(f"The tests did not finish on {sha} within 80 minutes.")


def served(arguments: argparse.Namespace) -> None:
    version: str = arguments.version
    sha256: str = arguments.sha256
    url = f"{arguments.index.rstrip('/')}/pypi/gateseal/{version}/json"

    def listed() -> str | None:
        try:
            return served_wheel(fetch_json(url), version, sha256)
        except OSError as error:
            print(f"{url}: {error}")
            return None

    wheel_url = poll(listed, INDEX_DEADLINE_SECONDS)
    if wheel_url is None:
        raise Refusal(f"{url} lists no wheel for {version} after 10 minutes.")
    with urllib.request.urlopen(wheel_url, timeout=120) as response:
        data: bytes = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise Refusal(f"The wheel at {wheel_url} does not have SHA-256 {sha256}.")
    destination = Path(arguments.dest)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"gateseal-{version}-py3-none-any.whl"
    path.write_bytes(data)
    print(path.resolve())


def notes(arguments: argparse.Namespace) -> None:
    print(changelog_section(Path("CHANGELOG.md").read_text(), arguments.version))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(required=True)
    commands.add_parser("check").set_defaults(run=check)
    commands.add_parser("wait").set_defaults(run=wait)
    served_command = commands.add_parser("served")
    served_command.add_argument("--version", required=True)
    served_command.add_argument("--sha256", required=True)
    served_command.add_argument("--dest", required=True)
    served_command.add_argument("--index", default="https://test.pypi.org")
    served_command.set_defaults(run=served)
    notes_command = commands.add_parser("notes")
    notes_command.add_argument("--version", required=True)
    notes_command.set_defaults(run=notes)
    arguments = parser.parse_args(argv)
    try:
        arguments.run(arguments)
    except Refusal as refusal:
        print(f"::error::{refusal}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
