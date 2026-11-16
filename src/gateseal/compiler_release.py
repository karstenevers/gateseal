"""The compiler release a Lock pins.

Its repository, version and commit, and the Python and pre-commit it carries.
"""

import functools
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.resources import files
from typing import Self
from urllib.parse import quote

from gateseal import __version__
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import relative_name
from gateseal.platforms import TARGETS, Target
from gateseal.validation import (
    decode_json,
    digest,
    fields,
    mapping,
    sequence,
    string,
    version,
)

RELEASES = "https://github.com/astral-sh/python-build-standalone/releases/download/"
"""Where python-build-standalone publishes each build's archives and SHA256SUMS."""


REPOSITORY = "https://github.com/karstenevers/gateseal"
"""gateseal's repository, whose release tags a Lock pins by commit."""

COMMIT = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class GatesealPin:
    """The gateseal release a Lock pins: its repository, version and commit.

    pre-commit builds gateseal's hook from the commit and CI installs gateseal
    from it, so the commit, not a wheel, names the code that checks a commit.
    """

    repository: str
    version: str
    commit: str

    def document(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "version": self.version,
            "commit": self.commit,
        }

    @classmethod
    def parse(cls, value: object) -> Self:
        item = fields(value, {"repository", "version", "commit"}, "gateseal")
        commit = string(item["commit"], "gateseal.commit")
        if not COMMIT.fullmatch(commit):
            raise GatesealError(
                "invalid_field", "gateseal.commit must be a full SHA-1 commit."
            )
        return cls(
            string(item["repository"], "gateseal.repository"),
            version(item["version"], "gateseal.version"),
            commit,
        )

    @property
    def running(self) -> bool:
        """Tell whether this is the gateseal that runs, from its repository."""
        return (self.repository, self.version) == (REPOSITORY, __version__)


@dataclass(frozen=True)
class Runtime:
    """The Python and pre-commit that run this compiler release.

    python holds uv's download metadata record of the one CPython release for
    each target, by uv's key. packages is pre-commit's wheel closure, pre-commit
    itself at version pre_commit among them, each with every wheel's hash.
    """

    minimum_uv: str
    python: Mapping[str, dict[str, object]]
    pre_commit: str
    packages: tuple[dict[str, object], ...]

    def interpreter(self, target: Target) -> dict[str, dict[str, object]]:
        """Return uv's download metadata with target's one record."""
        return for_target(self.python, target)

    def requirements(self) -> bytes:
        """Return pre-commit's closure as hash-pinned requirements."""
        lines: list[str] = []
        for package in self.packages:
            hashes = [
                "--hash=sha256:" + str(mapping(a, "artifact")["sha256"])
                for a in sequence(package["artifacts"], "artifacts")
            ]
            lines.append(f"{package['name']}=={package['version']} " + " ".join(hashes))
        return ("\n".join(lines) + "\n").encode("utf-8")


def validate_python(document: object) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for key, value in mapping(document, "python").items():
        entry = fields(
            value,
            {
                "name",
                "arch",
                "os",
                "libc",
                "major",
                "minor",
                "patch",
                "prerelease",
                "url",
                "sha256",
                "variant",
                "build",
            },
            "interpreter",
        )
        arch = fields(entry["arch"], {"family", "variant"}, "interpreter.arch")
        if entry["name"] != "cpython" or arch["variant"] is not None:
            raise GatesealError(
                "invalid_field", "Only ordinary CPython distributions are supported."
            )
        if entry["variant"] is not None or entry["prerelease"] != "":
            raise GatesealError(
                "invalid_field", "Only stable CPython distributions are supported."
            )
        ver = _interpreter_version(entry)
        target = f"{entry['os']}-{arch['family']}-{entry['libc']}"
        if key != f"cpython-{ver}-{target}":
            raise GatesealError(
                "invalid_field", "Python key disagrees with its distribution record."
            )
        build = string(entry["build"], "interpreter.build")
        if not re.fullmatch(r"[0-9]{8}", build):
            raise GatesealError(
                "invalid_field", "Interpreter build must be an eight-digit date."
            )
        _check_archive_url(entry["url"], build)
        digest(entry["sha256"], "interpreter.sha256")
        records[key] = entry
    return records


def _interpreter_version(entry: Mapping[str, object]) -> str:
    parts = [entry[p] for p in ("major", "minor", "patch")]
    if any(type(part) is not int or part < 0 for part in parts):
        raise GatesealError(
            "invalid_field", "Interpreter version components must be integers."
        )
    return ".".join(str(p) for p in parts)


def _check_archive_url(value: object, build: str) -> None:
    url = string(value, "interpreter.url")
    if not url.startswith(RELEASES + build + "/") or not url.endswith(".tar.gz"):
        raise GatesealError(
            "invalid_field", "Expected an official pinned Python archive URL."
        )
    if "?" in url or "#" in url or "/../" in url:
        raise GatesealError("invalid_field", "Unsafe Python archive URL.")


def interpreters(document: object, field: str) -> dict[str, dict[str, object]]:
    """Validate uv's records of one CPython release and build, one per target."""
    python = validate_python(document)
    releases = {
        (*(record[p] for p in ("major", "minor", "patch")), record["build"])
        for record in python.values()
    }
    if len(releases) != 1:
        raise GatesealError(
            "invalid_field", f"{field} must hold one CPython version and build."
        )
    if set(python) != {
        f"cpython-{cpython_release(python)}-{t.python}" for t in TARGETS.values()
    }:
        raise GatesealError(
            "invalid_field",
            f"{field} needs exactly one record for each supported target.",
        )
    return python


def cpython_release(python: Mapping[str, Mapping[str, object]]) -> str:
    """Return the CPython release of interpreters' records, such as 3.14.10."""
    record = next(iter(python.values()))
    return ".".join(str(record[part]) for part in ("major", "minor", "patch"))


def for_target(
    python: Mapping[str, dict[str, object]], target: Target
) -> dict[str, dict[str, object]]:
    """Return uv's download metadata with only target's record of python."""
    return {
        key: record
        for key, record in python.items()
        if key.endswith("-" + target.python)
    }


def interpreter_records(
    sums: str, version: str, build: str
) -> dict[str, dict[str, object]]:
    """Return uv's download metadata for every target from a build's SHA256SUMS."""
    major, minor, patch = (int(part) for part in version.split("."))
    records: dict[str, dict[str, object]] = {}
    for target in TARGETS.values():
        name = f"cpython-{version}+{build}-{target.platform}-install_only_stripped"
        filename = name + ".tar.gz"
        digests = [
            line.split()[0]
            for line in sums.splitlines()
            if line.split()[1:] == [filename]
        ]
        if len(digests) != 1:
            raise GatesealError(
                "upstream_archive_missing",
                f"SHA256SUMS of python-build-standalone build {build} has "
                f"{len(digests)} lines for {filename}, not one.",
            )
        system, family, libc = target.python.split("-")
        records[f"cpython-{version}-{target.python}"] = {
            "arch": {"family": family, "variant": None},
            "build": build,
            "libc": libc,
            "major": major,
            "minor": minor,
            "name": "cpython",
            "os": system,
            "patch": patch,
            "prerelease": "",
            "sha256": digests[0],
            "url": f"{RELEASES}{build}/{quote(filename)}",
            "variant": None,
        }
    return records


def validate_pre_commit_packages(
    closure: object, pre_commit: str
) -> tuple[dict[str, object], ...]:
    packages: list[dict[str, object]] = []
    names: set[str] = set()
    for value in sequence(closure, "pre-commit packages"):
        package = fields(value, {"name", "version", "artifacts"}, "pre-commit package")
        name = string(package["name"], "package.name")
        if not re.fullmatch("[a-z][a-z0-9-]*", name) or name in names:
            raise GatesealError(
                "invalid_field",
                "pre-commit package names must be unique normalized names.",
            )
        names.add(name)
        package_version = string(package["version"], "package.version")
        # https://peps.python.org/pep-0440/#appendix-b-parsing-version-strings-with-regular-expressions
        if not re.fullmatch(
            r"([1-9][0-9]*!)?(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))*"
            r"((a|b|rc)(0|[1-9][0-9]*))?(\.post(0|[1-9][0-9]*))?"
            r"(\.dev(0|[1-9][0-9]*))?",
            package_version,
        ):
            raise GatesealError(
                "invalid_field",
                "package.version must be a canonical public PEP 440 version.",
            )
        _check_wheels(package["artifacts"])
        packages.append(package)
    if not any(
        p["name"] == "pre-commit" and p["version"] == pre_commit for p in packages
    ):
        raise GatesealError(
            "invalid_field", "The pre-commit closure disagrees with its pin."
        )
    return tuple(packages)


def _check_wheels(value: object) -> None:
    artifacts = sequence(value, "artifacts")
    if not artifacts:
        raise GatesealError(
            "invalid_field", "A pre-commit package has no wheel hashes."
        )
    for artifact in artifacts:
        item = fields(artifact, {"filename", "url", "sha256"}, "pre-commit artifact")
        filename = relative_name(string(item["filename"], "filename"))
        if "/" in filename or not filename.endswith(".whl"):
            raise GatesealError("invalid_field", "Expected a wheel filename.")
        url = string(item["url"], "artifact.url")
        if not url.startswith(
            "https://files.pythonhosted.org/packages/"
        ) or not url.endswith(filename):
            raise GatesealError(
                "invalid_field", "Expected a credential-free PyPI wheel URL."
            )
        digest(item["sha256"], "artifact.sha256")


def parse_runtime(data: bytes) -> Runtime:
    """Validate runtime.lock: one CPython release for each target, and pre-commit."""
    document = fields(
        decode_json(data),
        {"generated_by", "minimum_uv", "python", "pre_commit"},
        "runtime",
    )
    string(document["generated_by"], "runtime.generated_by")
    python = interpreters(document["python"], "runtime.python")
    pre_commit = fields(document["pre_commit"], {"version", "packages"}, "pre_commit")
    pinned = version(pre_commit["version"], "pre_commit.version")
    return Runtime(
        version(document["minimum_uv"], "runtime.minimum_uv"),
        python,
        pinned,
        validate_pre_commit_packages(pre_commit["packages"], pinned),
    )


@functools.cache
def load_runtime() -> Runtime:
    """Return the runtime this compiler release carries."""
    return parse_runtime(
        files("gateseal").joinpath("data", "runtime.lock").read_bytes()
    )
