"""The policy's Node, which every kind whose hooks run on Node shares.

policy.toml's node pins one Node version for every tool on Node. gateseal
policy lock reads each platform's SHA-256 from the release's SHASUMS256.txt,
checks each archive that the Policy Lock lacks, and records the archives
under node. The Lock records the Node once, in native_artifacts.node, in the
record shape of a release binary.

gateseal policy lock also records npm's lockfile of each tool from npm.
gateseal's own hook, npm_tool, reads both records from the Lock and starts the
tool.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from gateseal.diagnostics import GatesealError
from gateseal.npm_tool import NODE
from gateseal.tools.mise import MINIMUM_MISE, check_archive, read_checksums
from gateseal.tools.model import (
    LockSection,
    MissingUpstream,
    Upstream,
    changed_upstream,
    lock_failure,
)
from gateseal.validation import canonical, digest, fields, mapping, nested, string

NAME = NODE
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

INTEGRITY = re.compile(r"sha(?:1|256|384|512)-[A-Za-z0-9+/]+={0,2}")
"""An npm integrity hash, which npm checks: a Subresource Integrity digest."""

PACKAGE = re.compile(r"(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*")
"""An npm package's name, scoped or not, so node_modules/<name> is its folder."""


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


@dataclass(frozen=True)
class Tree:
    """npm's lockfile of one install and its program."""

    lockfile: bytes
    program: str

    @property
    def dependencies(self) -> dict[str, str]:
        return json.loads(self.lockfile)["packages"][""]["dependencies"]


def read_tree(
    lockfile: object,
    dependencies: Mapping[str, str],
    package: str,
    executable: str,
    subject: str,
) -> Tree:
    """Return the Tree of npm's lockfile from a Policy Lock record of subject.

    A lockfile of other dependencies is stale. Each package needs an
    integrity hash, which npm checks, unless its parent bundles it.
    """
    found = mapping(lockfile, f"{subject}'s lockfile")
    packages = mapping(found.get("packages"), f"{subject}'s lockfile packages")
    locked = nested(packages, "", "dependencies")
    if locked != dependencies:
        raise lock_failure(
            "policy_lock_stale",
            f"policy.lock locks {json.dumps(locked)} for "
            f"{subject}, but its definition and policy.toml give "
            f"{json.dumps(dependencies, sort_keys=True)}.",
        )
    for path, entry in packages.items():
        hashed = INTEGRITY.fullmatch(str(nested(entry, "integrity")))
        if path and not nested(entry, "inBundle") and not hashed:
            raise GatesealError(
                "invalid_field",
                f"policy.lock locks {path} for {subject} without an integrity hash.",
            )
    program = nested(packages, f"node_modules/{package}", "bin", executable)
    if not isinstance(program, str):
        raise GatesealError(
            "upstream_executable_missing",
            f"The npm package {package} of {subject} links no program {executable}.",
            next_step=f"Set the executable name to a program that {package} links.",
        )
    return Tree(canonical(found), f"node_modules/{package}/{program}")


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
