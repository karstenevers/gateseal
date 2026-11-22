"""The policy's Node, which every kind whose hooks run on Node shares.

policy.toml's node pins one Node version for every tool on Node. gateseal
policy lock reads each platform's SHA-256 from the release's SHASUMS256.txt,
checks each archive that the Policy Lock lacks, and records the archives
under node. The Lock records the Node once, in native_artifacts.node, in the
record shape of a release binary.

A hook runs mise with --no-config, which installs the archive by URL and
SHA-256, then START: npm installs the exact packages once, without install
scripts, into a folder of the user's cache folder that the machine type, the
Node and the pins name, and the pinned Node starts the program. So no Node
on PATH, no Node project of the repository and no other package supplies the
Node, a package or the program.

Unlike a release binary, a hook on Node gets the caller's whole environment,
because npm needs the caller's proxy and CA settings, and npm reads the
user's own .npmrc. What the listed packages depend on is not pinned, and npm
records no integrity hash that a hook could enforce.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from gateseal.diagnostics import GatesealError
from gateseal.tools.mise import (
    MINIMUM_MISE,
    MISE,
    check_archive,
    mise_install_name,
    mise_request,
    read_checksums,
)
from gateseal.tools.model import (
    LockSection,
    MissingUpstream,
    Upstream,
    changed_upstream,
    lock_failure,
)
from gateseal.validation import (
    canonical,
    digest,
    fields,
    mapping,
    nested,
    sha,
    string,
)

NAME = "node"
"""The policy.toml field that pins Node, its Policy Lock record and its Lock entry."""

DIST = "https://nodejs.org/dist/v{version}"
"""The directory of one Node release's files."""

CHECKSUMS = "SHASUMS256.txt"
"""The release's checksum file, in sha256sum's format."""

ARCHIVES: Mapping[str, str] = {
    "linux-x64": "node-v{version}-linux-x64.tar.gz",
    "macos-arm64": "node-v{version}-darwin-arm64.tar.gz",
}
"""Each mise platform that gateseal prepares Node for, and its archive's name.

Its keys are platforms.TARGETS' values.
"""

EXECUTABLE = "bin/node"
"""The program in Node's install, as the Lock records it for each platform."""

REGISTRY = "https://registry.npmjs.org"
"""Where gateseal policy lock asks whether a package's release exists."""

NPM_INSTALL = (
    "npm",
    "install",
    "--no-save",
    "--no-package-lock",
    "--ignore-scripts",
    "--bin-links",
    "--install-strategy=hoisted",
    "--no-dry-run",
    "--no-package-lock-only",
    "--no-global",
    "--location=project",
    "--no-workspaces",
    "--include=optional",
    "--include=peer",
    "--no-audit",
    "--no-fund",
    "--no-update-notifier",
    "--no-usage",
    "--no-timing",
    "--logs-max=0",
)
"""Install packages into the folder that --prefix names, and write nothing else.

An option on the command line wins over the user's npm settings and over
each npm_config_ variable. --bin-links and --install-strategy=hoisted give
the layout that START and a loader read. The options from --no-dry-run to
--include=peer stop a user setting from making an install that is empty, in
another place or without a package, or that fails.
"""

REQUIRE_NODE = (
    "node=$(mise --no-config where {install} 2>/dev/null) && node=$node/bin && "
    '[ -x "$node/node" ] || {{ echo "mise has no install of Node {node}, '
    'the pinned Node. Turn the auto-install of mise on." >&2; exit 127; }}; '
    "PATH=$node:$PATH; "
)
"""Put the bin folder of the pinned install in node and first on PATH, or stop."""

START = REQUIRE_NODE + (
    "case ${{XDG_CACHE_HOME-}} in /*) cache=$XDG_CACHE_HOME ;; "
    "*) cache=$HOME/.cache ;; esac; "
    "dir=$cache/gateseal/npm/$(uname -s)-$(uname -m)/{folder}; "
    'has() {{ case $(readlink "$1/node_modules/.bin/$0" 2>/dev/null) in '
    '../{package}/*) [ -x "$1/node_modules/.bin/$0" ] ;; *) false ;; esac; }}; '
    '[ -e "$dir" ] || {{ '
    'mkdir -p "${{dir%/*}}" && new=$(mktemp -d "$dir.XXXXXX") || exit 127; '
    "(unset npm_config_workspace npm_config_workspaces "
    "NPM_CONFIG_WORKSPACE NPM_CONFIG_WORKSPACES; "
    '"$node"/{npm} --prefix "$new" --cache "$new/.npm" '
    '--logs-dir "$new/.npm/_logs" '
    "{packages} >/dev/null) || "
    '{{ rm -rf "$new"; exit 127; }}; '
    'rm -rf "$new/.npm"; '
    'has "$new" || {{ rm -rf "$new"; '
    'echo "npm installed no program $0 of {package}." >&2; exit 127; }}; '
    '"$node/node" -e "fs.renameSync(process.argv[1], process.argv[2])" '
    '"$new" "$dir" '
    '2>/dev/null || [ -e "$dir" ] || '
    '{{ rm -rf "$new"; echo "cannot write $dir" >&2; exit 127; }}; '
    'rm -rf "$new"; }}; '
    'has "$dir" || {{ echo "No program $0 of {package} in $dir. '
    'Delete the folder to install again." >&2; exit 127; }}; '
    "bin=$dir/node_modules/.bin; "
    'PATH="$node:$bin:$PATH" exec "$node/node" "$bin/$0" "$@"'
)
"""The shell script of a hook: install the packages once, then start $0.

It runs only on the Node that mise installed from the Lock's archive, which
mise where names, and stops at each use without one, as with mise's
auto-install off. So a report that names the Node of a hook is true.

Without the install folder, npm installs into a new folder beside it, with
its downloads and logs inside, and only a complete install that has the
program gets the final name, by one rename. So a half install is never used,
and two first uses at one time leave one install. The caller's workspace
selection, such as the one of npm run -w, is unset, because npm then fails
outside a workspace, also with --no-workspaces.

The program is the one that the first package links into node_modules/.bin.
When the script cannot start it, it stops with status 127, so the tool's
own exit codes, such as that of a finding, keep their meaning. The script
repairs no final folder, because another hook may run from it. The pinned
Node starts the program as its script, so its first line chooses no
interpreter, and no node_modules/.bin of the repository reaches its PATH.
"""

PACKAGE = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*")
"""An npm package's name, scoped or not.

It holds no @ but the scope's, so name@version is unambiguous, and no
character that a hook's entry would quote.
"""


@dataclass(frozen=True)
class Package:
    """One npm package at an exact version."""

    name: str
    version: str


def package_name(value: object, field: str) -> str:
    """Return an npm package's name, or fail with invalid_field naming field."""
    text = string(value, field)
    if not PACKAGE.fullmatch(text):
        raise GatesealError(
            "invalid_field",
            f"{field} must be an npm package's name, such as prettier or "
            f"@typescript-eslint/parser, not {text!r}.",
        )
    return text


def install_folder(node: str, packages: Sequence[Package]) -> str:
    """Return the folder of one install, relative to a machine type's folder.

    Its name holds the Node's version and a digest of the install command,
    so a change of the Node, of a pin or of NPM_INSTALL gives a new folder.
    START puts it under the folder of the operating system and processor.
    """
    words = [*NPM_INSTALL, *_specs(packages)]
    return f"node-{node}/{sha(canonical(words))[:16]}"


def _specs(packages: Sequence[Package]) -> list[str]:
    return [f"{package.name}@{package.version}" for package in packages]


def launch(
    node: str, install: str, packages: Sequence[Package], executable: str
) -> tuple[str, ...]:
    """Return the words that install packages once on Node node and start executable.

    install is the name that mise_install_name gives the Node's install. The
    packages keep the caller's order, so equal lists give equal bytes.
    executable is a bare program name that the first package installs.
    """
    script = START.format(
        folder=install_folder(node, packages),
        node=node,
        install=shlex.quote(install),
        npm=shlex.join(NPM_INSTALL),
        packages=shlex.join(_specs(packages)),
        package=packages[0].name,
    )
    return ("/bin/sh", "-c", script, executable)


@dataclass(frozen=True)
class NodeArchive:
    """One platform's Node archive: its derived URL and its SHA-256."""

    url: str
    sha256: str


@dataclass(frozen=True)
class Node:
    """The policy's Node at its pinned version, with an archive for each of ARCHIVES."""

    version: str
    platforms: Mapping[str, NodeArchive]

    @property
    def prefix(self) -> tuple[str, ...]:
        """Return the words that start any program on this Node, such as npm."""
        return (*MISE, mise_request(NAME, self.version, self.platforms), "--")

    def launch(self, packages: Sequence[Package], executable: str) -> tuple[str, ...]:
        """Return the command that installs packages once and starts executable."""
        install = mise_install_name(NAME, self.version, self.platforms)
        return (*self.prefix, *launch(self.version, install, packages, executable))

    def included(self, program: str) -> tuple[str, ...]:
        """Return the command that starts program of Node's distribution, such as npm.

        It starts program by its path in the pinned install, as START starts npm.
        """
        install = mise_install_name(NAME, self.version, self.platforms)
        script = REQUIRE_NODE.format(install=shlex.quote(install), node=self.version)
        script += 'exec "$node/$0" "$@"'
        return (*self.prefix, "/bin/sh", "-c", script, program)

    def lock_entry(self) -> tuple[LockSection, str, dict[str, object]]:
        """Return the Lock's native_artifacts entry of this Node."""
        return (
            "native_artifacts",
            NAME,
            {
                "version": self.version,
                "minimum_mise": MINIMUM_MISE,
                "platforms": {
                    platform: {
                        "url": archive.url,
                        "sha256": archive.sha256,
                        "executable": EXECUTABLE,
                    }
                    for platform, archive in self.platforms.items()
                },
            },
        )


def archive_url(version: str, platform: str) -> str:
    """Return the URL of version's archive for a mise platform of ARCHIVES."""
    archive = ARCHIVES[platform].format(version=version)
    return f"{DIST.format(version=version)}/{archive}"


def read_node(version: str, record: object) -> Node:
    """Return the Node that policy.toml pins, from its Policy Lock record.

    A record of another version or another set of platforms fails with
    policy_lock_stale, since gateseal policy lock rewrites both. Any other
    flaw fails with invalid_field.
    """
    found = fields(record, {"version", "platforms"}, NAME)
    platforms = mapping(found["platforms"], f"{NAME}.platforms")
    if found["version"] != version or set(platforms) != set(ARCHIVES):
        raise lock_failure(
            "policy_lock_stale",
            f"policy.lock records node {found['version']} for "
            f"{', '.join(sorted(platforms)) or 'no platform'}, but gateseal "
            f"prepares node {version}, which policy.toml pins, for "
            f"{', '.join(sorted(ARCHIVES))}.",
        )
    archives: dict[str, NodeArchive] = {}
    for platform, value in sorted(platforms.items()):
        at = f"{NAME}.platforms.{platform}"
        entry = fields(value, {"url", "sha256"}, at)
        url = archive_url(version, platform)
        if entry["url"] != url:
            raise GatesealError("invalid_field", f"{at}.url must be {url}.")
        archives[platform] = NodeArchive(url, digest(entry["sha256"], f"{at}.sha256"))
    return Node(version, archives)


def lock_node(version: str, recorded: object, upstream: Upstream) -> dict[str, object]:
    """Return the Policy Lock record of Node version, resolved from upstream.

    recorded is the lock's node record, or None. A record of another version
    is dropped, so a bump needs no deleted lock. For the same version, each
    recorded archive must equal what the checksum file lists now, or the lock
    fails with upstream_changed. Each archive the record lacks is downloaded
    and must have the listed SHA-256.
    """
    source = f"{CHECKSUMS} of {NAME} {version}"
    listing = upstream.download(f"{DIST.format(version=version)}/{CHECKSUMS}")
    if listing is None:
        raise MissingUpstream(
            "upstream_asset_missing",
            f"The {NAME} {version} release has no {CHECKSUMS}.",
            NAME,
            version,
        )
    listed = read_checksums(listing)
    platforms = {
        platform: _listed(listed, version, platform) for platform in sorted(ARCHIVES)
    }
    record: dict[str, object] = {"platforms": platforms, "version": version}
    kept = _kept(recorded, version, source, record)
    for platform, archive in platforms.items():
        if platform not in kept:
            check_archive(
                upstream, archive["url"], archive["sha256"], NAME, version, source
            )
        elif kept[platform] != archive:
            raise changed_upstream(
                NAME, version, source, {platform: kept[platform]}, {platform: archive}
            )
    return record


def _listed(listed: Mapping[str, str], version: str, platform: str) -> dict[str, str]:
    """Return one platform's archive record, with its SHA-256 from the listing."""
    url = archive_url(version, platform)
    archive = url.rsplit("/", 1)[1]
    if archive not in listed:
        raise MissingUpstream(
            "upstream_checksum_missing",
            f"{CHECKSUMS} of {NAME} {version} lists no {archive}, the {platform} "
            "archive.",
            NAME,
            version,
        )
    return {"sha256": listed[archive], "url": url}


def _kept(
    recorded: object, version: str, source: str, record: dict[str, object]
) -> dict[str, object]:
    """Return the archives that recorded holds for version, by platform.

    A record of another version keeps nothing. An unreadable record of the
    same version is a changed fact, as for a release binary.
    """
    if nested(recorded, "version") != version:
        return {}
    try:
        found = fields(recorded, {"version", "platforms"}, NAME)
        return mapping(found["platforms"], NAME)
    except GatesealError:
        raise changed_upstream(NAME, version, source, recorded, record) from None


def require_released(
    packages: Sequence[Package],
    known: Mapping[str, object],
    upstream: Upstream,
    tool: str,
    version: str,
) -> None:
    """Require the npm registry to have each package that known lacks.

    known maps a package's name to the version that the Policy Lock already
    records for it, so only a new pair costs a request. A scoped name goes as
    @scope%2fname, one path segment. A missing release fails with
    MissingUpstream for tool version.
    """
    for package in packages:
        if known.get(package.name) == package.version:
            continue
        name = package.name.replace("/", "%2f")
        if upstream.download(f"{REGISTRY}/{name}/{package.version}") is None:
            raise MissingUpstream(
                "upstream_release_missing",
                f"The npm registry has no release {package.version} of "
                f"{package.name}, which {tool} {version} installs.",
                tool,
                version,
                f"{package.name} {package.version}",
            )
