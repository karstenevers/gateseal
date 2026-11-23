"""The policy's Node, which every kind whose hooks run on Node shares.

policy.toml's node pins one Node version for every tool on Node. gateseal
policy lock reads each platform's SHA-256 from the release's SHASUMS256.txt,
checks each archive that the Policy Lock lacks, and records the archives
under node. The Lock records the Node once, in native_artifacts.node, in the
record shape of a release binary.

gateseal policy lock also records npm's lockfile of each tool from npm. A
hook runs mise with --no-config, which installs the archive by URL and
SHA-256, then START.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping
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
from gateseal.validation import canonical, digest, fields, mapping, nested, sha, string

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

NPM = (
    "npm_in() {{ "
    "grep -E '^[[:space:]]*(proxy|https-proxy|noproxy(\\[])?|cafile|registry|//[^=]+)"
    "[[:space:]]*=' "
    '"${{NPM_CONFIG_USERCONFIG:-${{npm_config_userconfig:-$HOME/.npmrc}}}}" '
    '> "$1/.npmrc" 2>/dev/null; folder=$(cd "$1" && pwd -P) || return; shift; '
    "env $(env | cut -d= -f1 | grep -iE '^npm_config_' | sed 's/^/-u /' "
    "| grep -viE ' npm_config_(proxy|https_proxy|noproxy|cafile|registry)$') "
    '"$node/node" "$node/npm" "$@" --prefix "$folder" '
    '--userconfig "$folder/.npmrc" --globalconfig /dev/null --cache "$folder/.npm" '
    "--ignore-scripts --no-audit --no-update-notifier --logs-max=0; }}; "
)
"""The shell function npm_in FOLDER ARGS: npm ARGS on the package.json in FOLDER.

npm reads only the user's proxy, CA and registry, from .npmrc and npm_config_
variables, and a registry's own .npmrc lines such as its token. env drops the
other variables, also those whose names a shell cannot unset. npm gets FOLDER's
real path: through a symlink, such as macOS's /var, npm writes broken lockfile paths.
"""

REQUIRE_NODE = (
    "node=$(mise --no-config where {install} 2>/dev/null) || "
    '{{ echo "mise has no install of Node {node}, the pinned Node. '
    'Turn the auto-install of mise on." >&2; exit 127; }}; '
    "node=$node/bin; PATH=$node:$PATH; "
)
"""Put the pinned Node's bin folder in node and first on PATH, or stop."""

FINISH = (
    "const [dir, digest, final] = process.argv.slice(1); "
    "const stop = (problem) => { console.error(problem); process.exit(1); }; "
    'const read = (name) => fs.readFileSync(path.join(dir, name), "utf8"); '
    'const lock = read("package-lock.json").replace(/\\r\\n/g, "\\n"); '
    'const hash = require("crypto").createHash("sha256").update(lock).digest("hex"); '
    'if (hash !== digest) stop("The lockfile is not the one that the hook names."); '
    "if (!final) process.exit(); "
    'const { packages } = JSON.parse(read("node_modules/.package-lock.json")); '
    'const npm = path.join(process.execPath, "../../lib/node_modules/npm"); '
    'const checks = require(path.join(npm, "node_modules/npm-install-checks")); '
    "for (const [name, entry] of Object.entries(JSON.parse(lock).packages)) { "
    "try { checks.checkPlatform(entry); } catch { continue; } "
    "if ((entry.os || entry.cpu || entry.libc) && !packages[name]) "
    "stop(`npm installed no ${name}.`); } "
    "try { fs.renameSync(dir, final); } catch {}"
)
"""node -e FINISH NEW DIGEST [DIR]: check the install NEW, and rename it to DIR.

NEW's lockfile, with LF line ends, must have the SHA-256 DIGEST. With DIR, npm's
record of the install must list each package made for this platform: npm ci exits
0 without one that it cannot download or whose hash differs. A rename fails only
when another first use took DIR.
"""

START = (
    REQUIRE_NODE
    + NPM
    + (
        "case ${{XDG_CACHE_HOME-}} in /*) cache=$XDG_CACHE_HOME ;; "
        "*) cache=$HOME/.cache ;; esac; "
        "dir=$cache/gateseal/npm/$(uname -s)-$(uname -m)/{folder}; "
        '[ -e "$dir" ] || {{ finish={finish}; '
        'mkdir -p "${{dir%/*}}" && new=$(mktemp -d "$dir.XXXXXX") || exit 127; '
        '{lockfile} > "$new/package-lock.json" && '
        '"$node/node" -e "$finish" "$new" {digest} && '
        'printf %s {manifest} > "$new/package.json" && '
        '(npm_in "$new" ci >/dev/null) && rm -rf "$new/.npm" "$new/.npmrc" && '
        '"$node/node" -e "$finish" "$new" {digest} "$dir" || {{ rm -rf "$new"; '
        'echo "npm cannot install the locked packages of $0." >&2; exit 127; }}; '
        'rm -rf "$new"; }}; '
        "PATH=$node:$dir/node_modules/.bin:$PATH "
        'exec "$node/node" "$dir/"{program} "$@"'
    )
)
"""The shell script of a hook: install the locked packages once, then start $0.

Without the install folder, npm ci installs the lockfile into a new folder
beside it, which FINISH names, so no half install is used. When npm cannot
install, the script stops with status 127, so the tool's own exit codes keep
their meaning. The pinned Node starts the program by its path, so neither its
first line nor a node_modules/.bin chooses what runs.
"""

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

    @property
    def prefix(self) -> tuple[str, ...]:
        """Return the words that start any program on this Node, such as npm."""
        return (*MISE, mise_request(NAME, self.version, self.platforms), "--")

    @property
    def install(self) -> str:
        """Return the name of the install that mise where finds."""
        return shlex.quote(mise_install_name(NAME, self.version, self.platforms))

    def launch(self, tree: Tree, path: str | None, executable: str) -> tuple[str, ...]:
        """Return the command that installs tree once and starts executable.

        path is the owned output that holds the tree's lockfile, or None for a
        command that holds the lockfile itself.
        """
        quoted = shlex.quote(path or tree.lockfile.decode())
        script = START.format(
            folder=f"node-{self.version}/{sha(NPM.encode() + tree.lockfile)[:16]}",
            node=self.version,
            install=self.install,
            lockfile=f"cat {quoted}" if path else f"printf %s {quoted}",
            manifest=shlex.quote(json.dumps({"dependencies": tree.dependencies})),
            program=shlex.quote(tree.program),
            finish=shlex.quote(FINISH),
            digest=sha(tree.lockfile),
        )
        return (*self.prefix, "/bin/sh", "-c", script, executable)

    def write_lockfile(self) -> tuple[str, ...]:
        """Return the command that locks the package.json of the folder that it gets."""
        script = (REQUIRE_NODE + NPM).format(install=self.install, node=self.version)
        script += 'npm_in "$1" install --package-lock-only >/dev/null'
        return (*self.prefix, "/bin/sh", "-c", script, "npm")

    def included(self, program: str) -> tuple[str, ...]:
        """Return the command that runs program of Node's distribution, such as npm."""
        script = REQUIRE_NODE.format(install=self.install, node=self.version)
        script += 'exec "$node/node" "$node/$0" "$@"'
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
