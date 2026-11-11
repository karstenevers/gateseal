"""The release-binary kind: archives on a release page with a checksum file.

Guarantees this module owns, whatever a definition says:

- Exact pin: the Policy Lock records, for each reviewed version and each
  mise platform the definition names, exactly the URL the definition derives,
  <repository>/releases/download/<tag>/<archive>, the definition's executable
  and the SHA-256 that gateseal policy lock read from the release's checksum
  file and checked against the downloaded archive.
- Verified acquisition: mise installs the archive inside the attempt by that
  URL and checksum, with no configuration file and no caller MISE_ setting.
  Commits never start mise.
- Version proof: activation and doctor run the linked executable's version
  arguments and refuse any other version, then run each hook's probe.
- No host fallback: each hook runs only the attempt's tools/gateseal-<name>
  link, which the record seals and commits check.
- Closed failure: an unsupported platform, a mise older than MINIMUM_MISE,
  or a failed probe stops with its next step, before the attempt or inside it.

Each version's Policy Lock record holds its archives by mise platform. The
Lock records each tool in native_artifacts under its name. The kind also owns
.gateseal/mise.toml.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar

from gateseal.bootstrap import TOOL_PREFIX, PreparedPaths, PreparedRecord, PreparedTool
from gateseal.compiler_release import TARGETS, Target
from gateseal.diagnostics import GatesealError
from gateseal.processes import execute, find_tool
from gateseal.tools.model import (
    OWNED,
    Catalog,
    Executable,
    Hook,
    LockSection,
    MissingUpstream,
    Release,
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
from gateseal.validation import (
    digest,
    fields,
    mapping,
    version_key,
)

MISE = OWNED + "/mise.toml"

MINIMUM_MISE = "2026.10.19"
"""The oldest mise that installs an archive as install and outputs ask it to."""


@dataclass(frozen=True)
class Archive:
    url: str
    sha256: str
    executable: str


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
    local: ClassVar[bool] = True
    python: ClassVar[bool] = False
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
        version: str,
        recorded: object,
        upstream: Upstream,
    ) -> dict[str, object]:
        """Read each platform's SHA-256 from the release's checksum file.

        An archive the record lacks is downloaded and must have that SHA-256.
        A recorded platform the definition no longer names is dropped.
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
            if platform in kept:
                if kept[platform] != archive:
                    raise changed_upstream(
                        name,
                        version,
                        source,
                        {platform: kept[platform]},
                        {platform: archive},
                    )
                continue
            _check_archive(upstream, archive, name, version, source)
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
        listed = _checksums(listing)
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
        archives: dict[str, Archive] = {}
        for platform, value in sorted(records.items()):
            at = f"{field}.platforms.{platform}"
            entry = fields(value, {"url", "sha256", "executable"}, at)
            url = self.url(version, platform)
            if entry["url"] != url or entry["executable"] != executable.name:
                raise GatesealError(
                    "invalid_field", f"{at} must name {url} and {executable.name}."
                )
            archives[platform] = Archive(
                url, digest(entry["sha256"], f"{at}.sha256"), executable.name
            )
        return archives


def _check_archive(
    upstream: Upstream, archive: Mapping[str, str], name: str, version: str, source: str
) -> None:
    """Download an archive and fail unless it has the SHA-256 that source lists."""
    data = upstream.download(archive["url"])
    if data is None:
        raise MissingUpstream(
            "upstream_asset_missing",
            f"The {name} {version} release has no {archive['url']}.",
            name,
            version,
        )
    if hashlib.sha256(data).hexdigest() != archive["sha256"]:
        raise GatesealError(
            "upstream_checksum_mismatch",
            f"{archive['url']} does not have the SHA-256 {archive['sha256']} "
            f"that {source} lists.",
            next_step=f"Do not review {name} {version} until its "
            "maintainers publish an archive that matches its checksum file.",
        )


def _checksums(listing: bytes) -> dict[str, str]:
    """Return each file's SHA-256 in a checksum file as sha256sum writes it."""
    listed: dict[str, str] = {}
    for line in listing.decode("utf-8", "replace").splitlines():
        checksum, _, name = line.partition(" ")
        name = name.lstrip(" *")
        if re.fullmatch("[0-9a-f]{64}", checksum) and name:
            listed[name] = checksum
    return listed


def _platforms(record: object) -> dict[str, object]:
    """Return a recorded version's archives by platform, or fail invalid_field."""
    return mapping(fields(record, {"platforms"}, "record")["platforms"], "platforms")


def parse_source(value: object, field: str) -> ReleaseBinary:
    table = fields(
        value, {"repository", "tag", "archive", "checksums", "platforms"}, field
    )
    supported = {target.mise for target in TARGETS.values()}
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

    def select(self, version: str) -> BinaryTool:
        return replace(self, version=version)

    def using(self, hooks: tuple[Hook, ...]) -> BinaryTool:
        return replace(self, use=replace(self.use, hooks=hooks))

    def entry(self, exclude: str | None, python: str | None) -> dict[str, object]:
        return local_repository(self.definition.name, self.use.hooks, exclude)

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
                    for platform, archive in release.platforms.items()
                },
            },
        )


def _own(tools: Iterable[object]) -> list[BinaryTool]:
    return sorted(
        (tool for tool in tools if isinstance(tool, BinaryTool)),
        key=lambda tool: tool.definition.name,
    )


def outputs(tools: Iterable[object]) -> dict[str, bytes]:
    """Return the kind's owned outputs: mise.toml, when a release binary is selected.

    It declares each selected release binary by name, with its version and
    each platform's URL and checksum.
    """
    binaries = _own(tools)
    if not binaries:
        return {}
    lines = [
        "# gateseal owns this file; gateseal configure writes it from gateseal.lock."
    ]
    for tool in binaries:
        name = json.dumps("http:" + tool.definition.name)
        if len(lines) > 1:  # pytestmut: skip[num.shift] -- the first tool sees one line
            lines.append("")
        lines += [
            f"[tools.{name}]",
            f"version = {json.dumps(tool.version)}",
            "",
            f"[tools.{name}.platforms]",
        ]
        for platform, archive in sorted(tool.release.platforms.items()):
            url, checksum = (
                json.dumps(archive.url),
                json.dumps("sha256:" + archive.sha256),
            )
            lines.append(f"{platform} = {{ url = {url}, checksum = {checksum} }}")
    return {MISE: ("\n".join(lines) + "\n").encode()}


@dataclass(frozen=True)
class NativeTool:
    """One locked native executable for this machine, which mise installs."""

    name: str
    version: str
    url: str
    sha256: str
    executable: str

    @property
    def spec(self) -> str:
        return (
            f"http:{self.name}[url={self.url},checksum=sha256:{self.sha256}]"
            f"@{self.version}"
        )


@dataclass(frozen=True)
class Mise:
    """The mise that plan checked, and each release binary's archive it installs.

    environment is the cleaned environment mise's own starts from.
    """

    executable: str
    environment: dict[str, str]
    tools: tuple[tuple[BinaryTool, NativeTool], ...]


def plan(tools: Iterable[object], target: Target, env: dict[str, str]) -> Mise | None:
    """Check this machine's archives and mise before any attempt exists.

    Without a release binary there is nothing to install and mise is never
    looked for. Otherwise each needs an archive for target, and mise must
    report at least MINIMUM_MISE.
    """
    binaries = _own(tools)
    if not binaries:
        return None
    natives: list[tuple[BinaryTool, NativeTool]] = []
    for tool in binaries:
        name = tool.definition.name
        archive = tool.release.platforms.get(target.mise)
        if archive is None:
            raise GatesealError(
                "unsupported_platform",
                f"Policy has no exact {name} archive for {target.mise}.",
            )
        native = NativeTool(
            name, tool.version, archive.url, archive.sha256, archive.executable
        )
        natives.append((tool, native))
    mise = find_tool("mise")
    # mise writes its own cache and state even to report its version, so they
    # go to a scratch directory rather than the user's home.
    with tempfile.TemporaryDirectory() as scratch:
        reported = execute(
            [mise, "--no-config", "version"],
            Path(scratch),
            dict(_mise_environment(Path(scratch), env), MISE_OFFLINE="1"),
        )
    try:
        supported = version_key(reported.stdout.decode().split()[0]) >= version_key(
            MINIMUM_MISE
        )
    except IndexError, GatesealError:
        supported = False
    if not supported:
        raise GatesealError(
            "mise_version_mismatch",
            f"gateseal requires mise {MINIMUM_MISE} or later.",
            4,
        )
    return Mise(mise, env, tuple(natives))


def _mise_environment(directory: Path, env: dict[str, str]) -> dict[str, str]:
    """Return an environment in which mise reads no caller settings or installs.

    mise --no-config reads no configuration file, and env holds no MISE_
    variable, so only the shared download cache is the caller's. mise checks
    each archive against the Lock's checksum, so a cached archive is safe to
    reuse. Without MISE_SYSTEM_DATA_DIR, mise treats a system install as
    installed.
    """
    env = dict(env)
    if "MISE_CACHE_DIR" in os.environ:
        env["MISE_CACHE_DIR"] = os.environ["MISE_CACHE_DIR"]
    env.update(
        {
            "MISE_DATA_DIR": str(directory / "data"),
            "MISE_STATE_DIR": str(directory / "state"),
            "MISE_SYSTEM_DATA_DIR": str(directory / "system"),
        }
    )
    return env


def install(
    mise: Mise, attempt: Path, paths: PreparedPaths, root: Path, env: dict[str, str]
) -> dict[str, tuple[Path, PreparedTool]]:
    """Install each release binary inside attempt, link it into tools, and probe it.

    Returns each tool by name with its install directory, which the record
    seals. Commits never start mise: mise exec runs any executable on PATH when
    the tool is missing, so the hooks run the linked binary directly. env is
    the hooks' environment, in which the probes run.
    """
    directory = attempt / "mise"
    directory.mkdir()
    mise_env = _mise_environment(directory, mise.environment)
    installed: dict[str, tuple[Path, PreparedTool]] = {}
    for tool, native in mise.tools:
        execute(
            [mise.executable, "--no-config", "install", native.spec],
            directory,
            mise_env,
        )
        where = execute(
            [mise.executable, "--no-config", "where", native.spec],
            directory,
            dict(mise_env, MISE_OFFLINE="1"),
        )
        location = _install_location(
            directory, where.stdout.decode().strip(), native.name
        )
        executable = location / native.executable
        info = executable.lstat() if os.path.lexists(executable) else None
        if (
            info is None
            or not stat.S_ISREG(info.st_mode)
            or not os.access(executable, os.X_OK)
        ):
            raise GatesealError(
                "invalid_preparation",
                f"mise did not install an executable {executable}.",
                4,
            )
        link = paths.tools / (TOOL_PREFIX + native.name)
        link.symlink_to(os.path.relpath(executable, paths.tools))
        _probe(tool, native.version, link, root, env)
        installed[native.name] = (location, PreparedTool(native.version, executable))
    return installed


def _install_location(directory: Path, reported: str, name: str) -> Path:
    """Resolve the directory mise reported for name, which must lie in directory."""
    location = Path(reported)
    if (
        not location.is_absolute()
        or location.is_symlink()
        or not location.is_dir()
        or directory.resolve() not in location.resolve().parents
    ):
        raise GatesealError(
            "invalid_preparation",
            f"mise installed {name} outside the attempt: {location}",
            4,
        )
    return location.resolve()


def _probe(
    tool: BinaryTool, version: str, link: Path, root: Path, env: dict[str, str]
) -> None:
    """Prove the version through the link, then run each hook's probe through it."""
    reported = execute([str(link), *tool.executable.version], root, env, stdin=b"")
    tool.executable.require_version(
        reported.stdout.decode().strip(), version, tool.definition.name
    )
    for hook in tool.use.hooks:
        execute([str(link), *hook.probe[1:]], root, env, stdin=b"")


def probe(
    tools: Iterable[object], record: PreparedRecord, root: Path, env: dict[str, str]
) -> None:
    """Re-prove every release binary the record holds, through its recorded link."""
    binaries = {tool.definition.name: tool for tool in _own(tools)}
    for name, prepared in record.native_tools.items():
        tool = binaries.get(name)
        if tool is None:
            raise GatesealError(
                "needs_activation",
                f"A prepared tool is {name}, which the Lock does not select.",
                3,
                "gateseal activate",
            )
        link = record.paths.tools / (TOOL_PREFIX + name)
        _probe(tool, prepared.version, link, root, env)
