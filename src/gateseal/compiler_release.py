"""The compiler release a Lock pins.

Its wheel, and the Python and pre-commit it carries.
"""

import functools
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from email.parser import BytesParser
from importlib.resources import files
from pathlib import Path
from typing import Self
from urllib.parse import quote, urlsplit

from gateseal import __version__
from gateseal.bootstrap import sha
from gateseal.diagnostics import GatesealError, Note
from gateseal.managed_files import read, relative_name
from gateseal.validation import (
    decode_json,
    digest,
    fields,
    mapping,
    sequence,
    string,
    version,
)


@dataclass(frozen=True)
class Target:
    """One supported machine, named as each tool that acquires for it names it.

    platform is the target triple of python-build-standalone and of uv's
    --python-platform.
    """

    python: str
    mise: str
    platform: str


TARGETS = {
    ("Darwin", "arm64"): Target(
        "darwin-aarch64-none", "macos-arm64", "aarch64-apple-darwin"
    ),
    ("Linux", "x86_64"): Target(
        "linux-x86_64-gnu", "linux-x64", "x86_64-unknown-linux-gnu"
    ),
}
"""The machines a compiler release carries a CPython for."""

RELEASES = "https://github.com/astral-sh/python-build-standalone/releases/download/"
"""Where python-build-standalone publishes each build's archives and SHA256SUMS."""


@dataclass(frozen=True)
class CompilerArtifact:
    kind: str
    location: str
    filename: str
    sha256: str
    version: str

    def document(self) -> dict[str, object]:
        return {
            "name": "gateseal",
            "version": self.version,
            "filename": self.filename,
            "sha256": self.sha256,
            "source": {"kind": self.kind, "location": self.location},
        }

    @classmethod
    def parse(cls, value: object) -> Self:
        item = fields(
            value, {"name", "version", "filename", "sha256", "source"}, "compiler"
        )
        if item["name"] != "gateseal":
            raise GatesealError(
                "invalid_field", "Compiler distribution must be gateseal."
            )
        release = version(item["version"], "compiler.version")
        filename = string(item["filename"], "compiler.filename")
        if filename != f"gateseal-{release}-py3-none-any.whl":
            raise GatesealError(
                "invalid_field", "Compiler wheel filename disagrees with its release."
            )
        checksum = digest(item["sha256"], "compiler.sha256")
        source = fields(item["source"], {"kind", "location"}, "compiler.source")
        kind = string(source["kind"], "compiler.source.kind")
        location = string(source["location"], "compiler.source.location")
        if kind == "local-wheel":
            if not Path(location).is_absolute() or Path(location).name != filename:
                raise GatesealError(
                    "invalid_field",
                    f"Local wheel {location} must be an absolute path to a file "
                    f"named {filename}.",
                )
        elif kind == "https-wheel":
            _check_https_wheel(location, filename)
        else:
            raise GatesealError(
                "unsupported_source",
                "Use local-wheel or https-wheel compiler transport.",
            )
        return cls(kind, location, filename, checksum, release)


PYPI = "files.pythonhosted.org"
"""The host that serves the files uploaded to PyPI, which PyPI never replaces."""

WHEEL_FOLDERS = {
    "raw.githubusercontent.com": (
        r"/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+/[0-9a-f]{40}/"
        r"(?:[A-Za-z0-9._-]+/)*"
    ),
    PYPI: r"/packages/[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{60}/",
}
"""The path before the wheel's name on each host whose files never change.

raw.githubusercontent.com serves /<owner>/<repository>/<commit>/<folders>/ at a
full commit. PyPI's folders are the file's BLAKE2b-256 digest split 2/2/60.
"""


def _check_https_wheel(location: str, filename: str) -> None:
    """Accept the wheel's URL on a host of WHEEL_FOLDERS, under its folders.

    The host is matched exactly, so userinfo or a port refuses the URL. A dot
    segment would let the URL leave the immutable folders.
    """
    try:
        url = urlsplit(location)
    except ValueError:
        url = None
    folders = None if url is None else WHEEL_FOLDERS.get(url.netloc)
    if (
        url is None
        or folders is None
        or url.scheme != "https"
        or url.query
        or url.fragment
        or not re.fullmatch(folders + re.escape(filename), url.path)
        or {".", ".."} & set(url.path.split("/"))
    ):
        raise GatesealError(
            "invalid_field",
            f"Use a credential-free wheel URL on {PYPI}, or on "
            "raw.githubusercontent.com at a full commit.",
        )


def verify_wheel(path: Path, artifact: CompilerArtifact) -> Path:
    """Check a wheel outside the repository and return its resolved path.

    Symlinked ancestors such as macOS /tmp are ordinary there, so the path is
    resolved before the strict reader walks it. uv installs the resolved file,
    so its own name must be the locked filename.
    """
    path = path.resolve()
    if path.name != artifact.filename:
        raise GatesealError(
            "invalid_artifact",
            f"Compiler wheel resolves to {path}, which is not named "
            f"{artifact.filename}.",
        )
    try:
        payload = read(path.parent, path.name)
    except FileNotFoundError as exc:
        raise GatesealError(
            "missing_artifact", f"No compiler wheel is at {path}.", 4
        ) from exc
    if sha(payload) != artifact.sha256:
        raise GatesealError(
            "artifact_hash_mismatch",
            "Local compiler wheel does not match its locked SHA-256.",
            4,
        )
    _check_metadata(path, artifact.version)
    return path


def _check_metadata(path: Path, release: str) -> None:
    """Check that the wheel holds gateseal release alone, without dependencies."""
    try:
        with zipfile.ZipFile(path) as wheel:
            metadata = [
                n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")
            ]
            if len(metadata) != 1:
                raise GatesealError(
                    "invalid_artifact", "Expected one compiler distribution."
                )
            message = BytesParser().parsebytes(wheel.read(metadata[0]))
            if (
                message["Name"] != "gateseal"
                or message["Version"] != release
                or message.get_all("Requires-Dist")
            ):
                raise GatesealError(
                    "invalid_artifact",
                    "Compiler must match the lock and have no runtime dependencies.",
                )
    except (OSError, zipfile.BadZipFile) as exc:
        raise GatesealError(
            "invalid_artifact", "Cannot inspect the compiler wheel."
        ) from exc


WHEEL = re.compile(r"gateseal-([0-9][0-9.]*)-py3-none-any\.whl")
"""A compiler wheel's file name, whose group is the release it holds."""


def artifact_from_flags(
    wheel: str | None,
    source: str | None,
    checksum: str | None,
    release: str | None = __version__,
) -> CompilerArtifact:
    """Return the compiler artifact that configure's or upgrade's flags name.

    release is the compiler release the wheel must hold. None takes it from
    the wheel's file name, as for an upgrade's candidate compiler, whose
    metadata verify_wheel then checks for a local wheel and uv for a URL.
    """
    if bool(wheel) == bool(source) or checksum is None:
        raise GatesealError(
            "missing_artifact",
            "Supply one --compiler-wheel or --compiler-source and --compiler-sha256.",
        )
    location = str(Path(wheel).resolve()) if wheel else str(source)
    if release is None:
        # pytestmut: off[num.shift] -- only the last part is read
        named = WHEEL.fullmatch(location.rsplit("/", 1)[-1])
        # pytestmut: on
        if named is None:
            raise GatesealError(
                "invalid_artifact",
                f"{location} is not a file named gateseal-<release>-py3-none-any.whl.",
            )
        release = str(named[1])
    item = CompilerArtifact(
        "local-wheel" if wheel else "https-wheel",
        location,
        f"gateseal-{release}-py3-none-any.whl",
        checksum,
        release,
    )
    item = CompilerArtifact.parse(item.document())
    if wheel:
        verify_wheel(Path(location), item)
    return item


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

    def document(self) -> dict[str, object]:
        """Return the Lock's runtime record."""
        return {
            "minimum_uv": self.minimum_uv,
            "python": dict(self.python),
            "pre_commit": {"version": self.pre_commit, "packages": list(self.packages)},
        }

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

    def note(self) -> Note:
        record = next(iter(self.python.values()))
        return Note(
            "info",
            "runtime",
            f"gateseal {__version__} runs CPython {cpython_release(self.python)} build "
            f"{record['build']} "
            f"and pre-commit {self.pre_commit} with {len(self.packages) - 1} pinned "
            "dependencies.",
        )


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
