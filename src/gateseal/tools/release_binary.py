"""The release-binary kind: archives on a release page with a checksum file.

Guarantees this module owns, whatever a definition says:

- Exact pin: the Policy Lock records, for each reviewed version and each
  mise platform the definition names, exactly the URL the definition derives,
  <repository>/releases/download/<tag>/<archive>, the definition's executable
  and the SHA-256 that gateseal policy lock read from the release's checksum
  file and checked against the downloaded archive. For an executable checked
  at commit it also records the SHA-256 of the executable in the archive.
- Verified acquisition: each hook starts its executable with mise exec, and
  the hook's entry names every platform's URL and checksum. mise installs the
  archive at its first use and checks the checksum. mise names an install by
  the tool's name and version, so the name carries a digest of the archives:
  requests share an install on a machine only when their archives are equal.
- No other source: mise runs with --no-config, so no mise file of the
  repository or of the user changes the archive or turns the install off.
- Clean environment: mise exec --deny-env passes the executable only the
  variables that mise keeps, such as HOME and PATH, and those that the caller
  names with --allow-env. A tool that asks a service also gets the proxy and
  certificate variables, which say how to reach a host, not what to check.
- Closed failure: a wrong checksum, a missing archive or a platform without
  an archive stops the hook.

mise never checks an installed tool again, and with its auto-install off it
starts an executable of the same name from the PATH. So for an executable
checked at commit, the commit check asks mise where it installed the tool's
request and compares the executable there with the recorded SHA-256.

Each version's Policy Lock record holds its archives by mise platform. The
Lock records each tool in native_artifacts under its name.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tarfile
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, TypeGuard

from gateseal.compiler_release import GatesealPin
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import LOCK
from gateseal.platforms import TARGETS
from gateseal.processes import execute
from gateseal.tools.mise import (
    MINIMUM_MISE,
    MISE,
    check_archive,
    mise_request,
    read_checksums,
)
from gateseal.tools.model import (
    Catalog,
    Executable,
    Hook,
    LockSection,
    MissingUpstream,
    Release,
    Starts,
    ToolDefinition,
    ToolUse,
    Upstream,
    changed_upstream,
    https_url,
    local_repository,
    lock_failure,
    pattern,
    unsupported,
)
from gateseal.validation import digest, fields, mapping, sha, string

if TYPE_CHECKING:
    from gateseal.tools.node import Node

MISE_EXEC = (*MISE, "--deny-env")
"""Start an executable with only the variables that mise keeps and the caller names."""

NETWORK = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
)
"""The proxy and certificate variables that a tool which asks a service gets."""

PROGRAM = "executable_sha256"
"""The record field of the executable's SHA-256 in an archive, for a check at commit."""


@dataclass(frozen=True)
class Archive:
    """One platform's archive. executable_sha256 is set for a check at commit."""

    url: str
    sha256: str
    executable: str
    executable_sha256: str | None


@dataclass(frozen=True)
class ArchiveRelease(Release):
    platforms: Mapping[str, Archive]


@dataclass(frozen=True)
class ReleaseBinary:
    """The [source] of a release binary.

    archive is the archive's file name pattern, with {platform} and
    optionally {version}; checksums names the checksum file, optionally with
    {version}. platforms maps each supported mise platform to the name the
    release gives it, which replaces {platform}.
    """

    kind: ClassVar[str] = "release-binary"
    starts: ClassVar[Starts] = Starts.RECORDED_EXECUTABLE
    remark: ClassVar[str] = ""
    on_node: ClassVar[bool] = False
    scans_history: ClassVar[bool] = True
    repository: str
    tag: str
    archive: str
    checksums: str
    platforms: Mapping[str, str]

    def release(self, version: str) -> str:
        """Return the URL of the directory of version's release files."""
        tag = self.tag.replace("{version}", version)
        return f"{self.repository}/releases/download/{tag}"

    def url(self, version: str, platform: str) -> str:
        """Return the URL of the archive for version and a mise platform."""
        archive = self.archive.replace("{version}", version)
        archive = archive.replace("{platform}", self.platforms[platform])
        return f"{self.release(version)}/{archive}"

    def offer(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        records: Mapping[str, object],
        node: Node | None,
    ) -> BinaryTool:
        """Read each version's archives, one for every platform, from its record."""
        executable = definition.executable
        if executable is None:
            raise GatesealError(
                "invalid_field", f"tools.{definition.name} names no executable."
            )
        versions = {
            version: ArchiveRelease(
                version,
                requires,
                self._archives(definition.name, executable, version, records[version]),
            )
            for version, requires in catalog.requires.items()
        }
        return BinaryTool(
            definition, self, executable, use, catalog.pin, versions, catalog.pin
        )

    def lock(
        self,
        definition: ToolDefinition,
        use: ToolUse,
        catalog: Catalog,
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Read each platform's SHA-256 from the release's checksum file.

        An archive the record lacks is downloaded and must have that SHA-256.
        For an executable checked at commit, an archive whose record lacks a
        SHA-256 of the executable is downloaded too, to read it. A recorded
        platform the definition no longer names is dropped, and so is a
        recorded executable SHA-256 the definition no longer checks.
        """
        name, executable = definition.name, definition.executable
        if executable is None:
            raise GatesealError("invalid_field", f"tools.{name} names no executable.")
        checksums = self.checksums.replace("{version}", version)
        platforms = self._listed_archives(
            name, executable.name, version, checksums, upstream
        )
        record: dict[str, object] = {"platforms": platforms}
        source = f"{checksums} of {name} {version}"
        try:
            kept = {} if recorded is None else _platforms(recorded)
        except GatesealError:
            raise changed_upstream(name, version, source, recorded, record) from None
        for platform, archive in platforms.items():
            _lock_archive(
                upstream,
                (platform, archive),
                kept.get(platform, {}),
                executable.check_at_commit,
                (name, version, source),
            )
        return record

    def _listed_archives(
        self,
        name: str,
        executable: str,
        version: str,
        checksums: str,
        upstream: Upstream,
    ) -> dict[str, dict[str, str]]:
        """Return each platform's archive record, with its SHA-256 from checksums."""
        listing = upstream.download(f"{self.release(version)}/{checksums}")
        if listing is None:
            raise MissingUpstream(
                "upstream_asset_missing",
                f"The {name} {version} release has no {checksums}.",
                name,
                version,
            )
        listed = read_checksums(listing)
        platforms: dict[str, dict[str, str]] = {}
        for platform in sorted(self.platforms):
            url = self.url(version, platform)
            archive = url.rsplit("/", 1)[1]
            if archive not in listed:
                raise MissingUpstream(
                    "upstream_checksum_missing",
                    f"{checksums} of {name} {version} lists no {archive}, the "
                    f"{platform} archive.",
                    name,
                    version,
                )
            platforms[platform] = {
                "executable": executable,
                "sha256": listed[archive],
                "url": url,
            }
        return platforms

    def _archives(
        self, name: str, executable: Executable, version: str, record: object
    ) -> dict[str, Archive]:
        """Validate one version's archive records, which must be the derived ones.

        Every platform the definition names needs a record; a record for
        another fails as policy_lock_stale, since gateseal policy lock drops it.
        """
        field = f"components.{name}.versions.{version}"
        records = mapping(fields(record, {"platforms"}, field)["platforms"], field)
        if set(records) != set(self.platforms):
            raise lock_failure(
                "policy_lock_stale",
                f"policy.lock records {name} {version} for "
                f"{', '.join(sorted(records)) or 'no platform'}, but gateseal "
                f"prepares it for {', '.join(sorted(self.platforms))}.",
            )
        checked = {PROGRAM} if executable.check_at_commit else set[str]()
        archives: dict[str, Archive] = {}
        for platform, value in sorted(records.items()):
            at = f"{field}.platforms.{platform}"
            entry = fields(value, {"url", "sha256", "executable"} | checked, at)
            url = self.url(version, platform)
            if entry["url"] != url or entry["executable"] != executable.name:
                raise GatesealError(
                    "invalid_field", f"{at} must name {url} and {executable.name}."
                )
            archives[platform] = Archive(
                url,
                digest(entry["sha256"], f"{at}.sha256"),
                executable.name,
                digest(entry[PROGRAM], f"{at}.{PROGRAM}") if checked else None,
            )
        return archives


def _lock_archive(
    upstream: Upstream,
    listed: tuple[str, dict[str, str]],
    recorded: Mapping[str, object],
    checked: bool,
    release: tuple[str, str, str],
) -> None:
    """Complete one platform's archive record from its recorded one or a download.

    listed is the platform and the archive record that the checksum file gives.
    release names the tool, its version and the checksum file.
    """
    (platform, archive), (name, version, source) = listed, release
    known = dict(recorded)
    program = known.pop(PROGRAM, None)
    if known and known != archive:
        raise changed_upstream(
            name, version, source, {platform: known}, {platform: archive}
        )
    if known and not checked:
        return
    if known and _is_digest(program):
        archive[PROGRAM] = program
        return
    data = check_archive(
        upstream, archive["url"], archive["sha256"], name, version, source
    )
    if checked:
        archive[PROGRAM] = _program_sha256(data, archive, name, version)


def _is_digest(value: object) -> TypeGuard[str]:
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def _program_sha256(
    data: bytes, archive: Mapping[str, str], name: str, version: str
) -> str:
    """Return the SHA-256 of the executable at the top of a tar.gz archive.

    mise unpacks the archive into the tool's install, so the executable there
    has these bytes. Of members with one name, the last is the one unpacked.
    """
    executable = archive["executable"]
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as bundle:
            members = [
                member
                for member in bundle.getmembers()
                if member.name.removeprefix("./") == executable
            ]
            last = members[-1] if members else None
            program = bundle.extractfile(last) if last and last.isfile() else None
            if program is not None:
                return hashlib.sha256(program.read()).hexdigest()
    except (tarfile.TarError, OSError, EOFError):
        pass
    raise GatesealError(
        "upstream_executable_missing",
        f"{archive['url']} is not a tar.gz archive with the file {executable} at "
        "its top.",
        next_step=f"Check that {name} {version} publishes {executable} at the top "
        "of a tar.gz archive, or stop checking it at commit.",
    )


def _platforms(record: object) -> dict[str, dict[str, object]]:
    """Return a recorded version's archives by platform, or fail invalid_field."""
    platforms = fields(record, {"platforms"}, "record")["platforms"]
    return {
        platform: mapping(archive, platform)
        for platform, archive in mapping(platforms, "platforms").items()
    }


def parse_source(value: object, field: str) -> ReleaseBinary:
    table = fields(
        value, {"repository", "tag", "archive", "checksums", "platforms"}, field
    )
    supported = set(TARGETS.values())
    platforms: dict[str, str] = {}
    for platform, name in mapping(table["platforms"], f"{field}.platforms").items():
        if platform not in supported:
            raise unsupported(f"{field}.platforms", platform)
        platforms[platform] = pattern(name, f"{field}.platforms.{platform}", ())
    if not platforms:
        raise GatesealError("invalid_field", f"{field}.platforms names no platform.")
    return ReleaseBinary(
        https_url(table["repository"], f"{field}.repository"),
        pattern(table["tag"], f"{field}.tag", ("version",)),
        pattern(table["archive"], f"{field}.archive", ("platform",), ("version",)),
        pattern(table["checksums"], f"{field}.checksums", (), ("version",)),
        platforms,
    )


@dataclass(frozen=True)
class BinaryTool:
    """A release binary as a policy offers it, at its selected version."""

    definition: ToolDefinition
    source: ReleaseBinary
    executable: Executable
    use: ToolUse
    pin: str
    versions: Mapping[str, ArchiveRelease]
    version: str

    @property
    def release(self) -> ArchiveRelease:
        return self.versions[self.version]

    @property
    def node(self) -> None:
        return None

    def select(self, version: str) -> BinaryTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> BinaryTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    @property
    def mise_request(self) -> str:
        return mise_request(self.definition.name, self.version, self.release.platforms)

    def program(self, variables: Collection[str] = ()) -> tuple[str, ...]:
        """Return the command that installs the tool if needed and starts it."""
        variables = (*variables, *(NETWORK if self.definition.network else ()))
        flags = (flag for name in variables for flag in ("--allow-env", name))
        return (*MISE_EXEC, *flags, self.mise_request, "--", self.executable.name)

    def entry(self, exclude: str | None, pin: GatesealPin) -> dict[str, object]:
        definition = self.definition
        return local_repository(
            self.program(), self.use.hooks, exclude, definition.stages, definition.scope
        )

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        release = self.release
        return (
            "native_artifacts",
            self.definition.name,
            {
                "version": release.version,
                "minimum_mise": MINIMUM_MISE,
                "platforms": {
                    platform: {
                        "url": archive.url,
                        "sha256": archive.sha256,
                        "executable": archive.executable,
                    }
                    | (
                        {}
                        if archive.executable_sha256 is None
                        else {PROGRAM: archive.executable_sha256}
                    )
                    for platform, archive in release.platforms.items()
                },
            },
        )


@dataclass(frozen=True)
class InstalledProgram:
    """An executable checked at commit, as the Lock records it for one platform.

    request is mise's request in the tool's hooks, and sha256 the SHA-256 of
    the executable in that platform's archive.
    """

    tool: str
    version: str
    request: str
    executable: str
    sha256: str


def checked_programs(
    native: Mapping[str, object], platform: str
) -> tuple[InstalledProgram, ...]:
    """Return each executable that the Lock's native_artifacts check on platform."""
    programs: list[InstalledProgram] = []
    for name, value in sorted(native.items()):
        field = f"native_artifacts.{name}"
        record = fields(value, {"version", "minimum_mise", "platforms"}, field)
        version = string(record["version"], f"{field}.version")
        archives: dict[str, Archive] = {}
        for key, entry in mapping(record["platforms"], f"{field}.platforms").items():
            at = f"{field}.platforms.{key}"
            table = mapping(entry, at)
            names = {"url", "sha256", "executable"} | ({PROGRAM} & set(table))
            table = fields(table, names, at)
            archives[key] = Archive(
                string(table["url"], f"{at}.url"),
                digest(table["sha256"], f"{at}.sha256"),
                string(table["executable"], f"{at}.executable"),
                digest(table[PROGRAM], f"{at}.{PROGRAM}") if PROGRAM in table else None,
            )
        here = archives.get(platform)
        if here is not None and here.executable_sha256 is not None:
            request = mise_request(name, version, archives)
            programs.append(
                InstalledProgram(
                    name, version, request, here.executable, here.executable_sha256
                )
            )
    return tuple(programs)


def check_installs(programs: Iterable[InstalledProgram], root: Path) -> None:
    """Fail unless each checked executable that mise installed has its SHA-256.

    mise --no-config where finds the install of the hooks' request, and mise
    which the program in it that the hooks' mise exec starts, which is in the
    install's bin folder when it has one. Neither uses the network. A tool
    that mise has not installed is skipped, and so is every tool without a
    mise on PATH: no hook of the tool can start it, and at its first use mise
    installs it from the archive that mise checks.
    """
    mise = shutil.which("mise")
    if mise is None:
        return
    env = dict(os.environ, MISE_OFFLINE="1")
    for program in programs:
        where = execute(
            [mise, "--no-config", "where", program.request],
            root,
            env,
            allow_failure=True,
        )
        if where.returncode:
            if b" not installed" in where.stderr:
                continue
            raise GatesealError(
                "tool_failed",
                f"mise did not say where it installed {program.tool}: "
                + where.stderr.decode("utf-8", "replace").strip(),
                next_step="Fix what mise reports, then commit again.",
            )
        folder = Path(where.stdout.decode().strip())
        name = Path(program.executable).name
        which = execute(
            [mise, "--no-config", "which", name, "--tool", program.request],
            root,
            env,
            allow_failure=True,
        )
        path = Path(which.stdout.decode().strip())
        tool = f"{program.tool} {program.version}"
        if which.returncode:
            problem = f"mise starts no {name} from {folder}, where it installed {tool}."
        elif not path.is_file() or sha(path.read_bytes()) != program.sha256:
            problem = f"{path} is not the {tool} program that {LOCK} records."
        else:
            continue
        raise GatesealError(
            "installed_tool_changed",
            problem,
            next_step=f"Find out what changed it. Then delete {folder} and "
            f"commit again, so that the {program.tool} hook installs it again "
            "from its checked archive.",
        )
