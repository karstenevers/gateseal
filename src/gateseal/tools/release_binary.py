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
  names with --allow-env.
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
import stat
import tarfile
import tempfile
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, TypeGuard

from gateseal.bootstrap import (
    LOCK,
    TOOL_PREFIX,
    PreparedPaths,
    PreparedRecord,
    PreparedTool,
    sha,
)
from gateseal.diagnostics import GatesealError
from gateseal.paths import resolved
from gateseal.platforms import TARGETS, Target
from gateseal.processes import execute, find_tool
from gateseal.tools.model import (
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
    string,
    version_key,
)

MISE_EXEC = ("mise", "--no-config", "exec", "--deny-env")
"""Start an executable from a tool that the command names, and from no mise file."""

MINIMUM_MISE = "2026.10.19"
"""The oldest mise that installs an archive as a hook's entry asks it to."""


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
    local: ClassVar[bool] = True
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


def _check_archive(
    upstream: Upstream, archive: Mapping[str, str], name: str, version: str, source: str
) -> bytes:
    """Download an archive and return it, once it has the SHA-256 that source lists."""
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
    return data


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
    data = _check_archive(upstream, archive, name, version, source)
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


def _checksums(listing: bytes) -> dict[str, str]:
    """Return each file's SHA-256 in a checksum file as sha256sum writes it."""
    listed: dict[str, str] = {}
    for line in listing.decode("utf-8", "replace").splitlines():
        checksum, _, name = line.partition(" ")
        name = name.lstrip(" *")
        if re.fullmatch("[0-9a-f]{64}", checksum) and name:
            listed[name] = checksum
    return listed


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

    @property
    def mise_request(self) -> str:
        return mise_request(self.definition.name, self.version, self.release.platforms)

    def program(self, variables: Collection[str] = ()) -> tuple[str, ...]:
        """Return the command that installs the tool if needed and starts it."""
        flags = (flag for name in variables for flag in ("--allow-env", name))
        return (*MISE_EXEC, *flags, self.mise_request, "--", self.executable.name)

    def entry(self, exclude: str | None) -> dict[str, object]:
        return local_repository(
            self.program(), self.use.hooks, exclude, self.definition.stages
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


def mise_request(name: str, version: str, platforms: Mapping[str, Archive]) -> str:
    """Return mise's request for a tool, with every platform's archive.

    mise reads the option platforms_<os>_<arch>_<name> as the nested
    platforms.<os>-<arch>.<name> of a mise file. A URL, a checksum and a
    version hold no comma or bracket, which would end an option. mise
    installs a request once under its tool name and version and never
    reads its options again, so the tool name ends in a digest of them.
    """
    options = ",".join(
        f"platforms_{platform.replace('-', '_')}_{option}={value}"
        for platform, archive in sorted(platforms.items())
        for option, value in (
            ("url", archive.url),
            ("checksum", "sha256:" + archive.sha256),
        )
    )
    digest = hashlib.sha256(options.encode()).hexdigest()[:12]
    return f"http:{name}-{digest}[{options}]@{version}"


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


def _own(tools: Iterable[object]) -> list[BinaryTool]:
    return sorted(
        (tool for tool in tools if isinstance(tool, BinaryTool)),
        key=lambda tool: tool.definition.name,
    )


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
    return Mise(require_mise(env), env, tuple(natives))


def require_mise(env: dict[str, str]) -> str:
    """Return the mise on PATH once it reports MINIMUM_MISE or later."""
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
    except (IndexError, GatesealError):
        supported = False
    if not supported:
        raise GatesealError(
            "mise_version_mismatch",
            f"gateseal requires mise {MINIMUM_MISE} or later.",
            4,
        )
    return mise


_FOUND = {"missing_prerequisite": "none is", "mise_version_mismatch": "an older one is"}
"""What the PATH holds for each way require_mise finds no usable mise."""


def mise_problem(need: str, error: GatesealError) -> str:
    """Complete need with what require_mise found, or report mise's own failure."""
    found = _FOUND.get(error.code)
    if found is None:
        return f"The mise on PATH did not report its version: {str(error).strip()}"
    return f"{need} mise {MINIMUM_MISE} or later, and {found} on PATH."


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
        or resolved(directory) not in resolved(location).parents
    ):
        raise GatesealError(
            "invalid_preparation",
            f"mise installed {name} outside the attempt: {location}",
            4,
        )
    return resolved(location)


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
