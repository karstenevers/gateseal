"""Author a Policy: start one, review and pin versions, lock and check it.

gateseal policy lock and gateseal policy add are the only commands that read
facts from upstream. They resolve each reviewed version through its Tool Kind
and write the Policy Lock. Every other command, gateseal policy check
included, loads a policy offline and refuses a lock that disagrees with
policy.toml.
"""

import fcntl
import http.client
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

from gateseal import hook_python, policy_toml, sources
from gateseal.diagnostics import GatesealError, Note, Result
from gateseal.managed_files import open_directory, read, read_optional, write_atomic
from gateseal.policy import (
    LOCK,
    LOCK_COMMAND,
    POLICY,
    Policy,
    Review,
    bundled_root,
    load_bundled_policy,
    load_policy,
    require_compatible,
    review_policy,
)
from gateseal.processes import find_tool
from gateseal.tools.model import Catalog, MissingUpstream, Release
from gateseal.validation import canonical, decode_json, mapping
from gateseal.validation import version as stable_version

README = "README.md"
"""The guide policy init writes beside the policy, which gateseal never reads."""

TIMEOUT = 60  # pytestmut: skip[num.shift] -- any timeout near a minute serves
"""Seconds a download may wait for the server before it fails."""


class Releases:
    """The upstream gateseal policy lock and add read: Git tags and release files.

    Tags come through the user's Git, as a policy release does, so Git's
    credentials and url.<base>.insteadOf apply. Release files come over HTTPS
    with the proxy and trust settings Python reads from the environment.
    A failed read's next step is to rerun the command that started it.
    """

    def __init__(self, git: str, scratch: Path, rerun: str) -> None:
        self.git = git
        self.scratch = scratch
        self.rerun = rerun

    def tagged(self, repository: str, tag: str) -> str | None:
        try:
            with sources.remote(
                self.git, self.scratch, repository, subject="the repository"
            ) as origin:
                return origin.peeled(tag)
        except GatesealError as exc:
            raise GatesealError(
                "upstream_unavailable",
                str(exc),
                4,
                f"Check that git ls-remote {repository} works, then rerun "
                f"{self.rerun}.",
            ) from exc

    def download(self, url: str) -> bytes | None:
        try:
            with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            detail = f"HTTP {exc.code}"
        except (OSError, http.client.HTTPException) as exc:
            detail = str(exc) or type(exc).__name__
        raise GatesealError(
            "upstream_unavailable",
            f"Cannot download {url}: {detail}",
            4,
            f"Check that this machine reaches {url}, then rerun {self.rerun}.",
        )


class Snapshot:
    """The policy files a command read, which must not change before it writes.

    A command reads each file once and may then wait on upstream for a minute.
    It writes only while every file it read still holds what it read, so an
    edit made meanwhile, by a person or another command, is never replaced by
    an older copy.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.seen: dict[str, bytes | None] = {}

    def read_optional(self, name: str) -> bytes | None:
        if name not in self.seen:
            self.seen[name] = read_optional(self.root, name, non_executable=True)
        return self.seen[name]

    def read(self, name: str) -> bytes:
        data = self.read_optional(name)
        if data is None:
            raise FileNotFoundError(name)
        return data

    def write(self, files: dict[str, bytes]) -> tuple[str, ...]:
        """Write each file whose content changed, in order, and return their names.

        A lock on the policy directory makes the comparison and the writes one
        step for every policy command. A file keeps its permissions, and a new
        one is written as init writes it.
        """
        changed: list[str] = []
        with open_directory(self.root) as directory:
            fcntl.flock(directory, fcntl.LOCK_EX)
            for name, data in self.seen.items():
                if read_optional(self.root, name, non_executable=True) != data:
                    raise GatesealError(
                        "policy_changed",
                        f"{name} changed while this command ran, so it wrote nothing.",
                        next_step="Run the command again.",
                    )
            for name, new in files.items():
                old, path = self.read_optional(name), self.root / name
                if new != old:
                    mode = 0o644
                    if old is not None:
                        # pytestmut: off[num.shift] -- reading it refused execute bits
                        mode = path.stat().st_mode & 0o666
                        # pytestmut: on
                    write_atomic(path, new, mode)
                    changed.append(name)
        return tuple(changed)


def _note(policy: Policy) -> Note:
    return Note(
        "info",
        "policy",
        f"Policy {policy.release}, digest {policy.content_digest}.",
    )


def init(root: Path) -> Result:
    """Write the bundled reference policy and its README into a new directory.

    The directory may exist only when it is empty, so init never replaces a
    file.
    """
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise GatesealError(
            "directory_not_empty",
            f"{root} is not empty; policy init writes only into a new or empty "
            "directory.",
            next_step="Name a new or empty directory.",
        )
    policy = load_bundled_policy()
    files = {asset.path: asset.content for asset in policy.assets}
    # pytestmut: off[bool.literal] -- the wheel's README is never executable
    files[README] = read(bundled_root(), README, non_executable=True)
    # pytestmut: on
    for name, content in files.items():
        (root / name).parent.mkdir(exist_ok=True)
        write_atomic(root / name, content, 0o644)
    return Result(
        "policy init",
        "initialized",
        f"Started a policy in {root} from the bundled policy.",
        changed=tuple(files),
        next_steps=(
            f"Read {README}, set release in policy.toml, and edit policy.toml "
            "and rules/. Then run gateseal policy lock.",
        ),
        notes=(_note(load_policy(Snapshot(root).read)),),
    )


def _recorded(data: bytes | None) -> dict[str, dict[str, object]]:
    """Return each version's record in an existing lock, by tool and version.

    Only the structure that holds the records must be intact; each kind
    compares a record with upstream itself.
    """
    if data is None:
        return {}
    try:
        components = mapping(decode_json(data).get("components"), "components")
        return {
            name: mapping(
                mapping(value, f"components.{name}").get("versions"),
                f"components.{name}.versions",
            )
            for name, value in components.items()
        }
    except GatesealError as exc:
        raise GatesealError(
            "policy_lock_edited",
            f"{LOCK} cannot be read, so its facts cannot be kept: {exc}",
            next_step=f"Restore {LOCK} from Git, or delete it to resolve every "
            f"reviewed version again, then run {LOCK_COMMAND}.",
        ) from exc


def _resolve(
    reader: Callable[[str], bytes], existing: bytes | None, rerun: str
) -> tuple[bytes, Policy]:
    """Return the Policy Lock of every version the policy reviews, and the policy.

    A fact the existing lock records is kept and must equal upstream's; only
    a version or platform without one gets new facts. The new lock must pass
    the check every load runs.
    """
    review = review_policy(reader)
    recorded = _recorded(existing)
    with tempfile.TemporaryDirectory(prefix="gateseal-") as scratch:
        upstream = Releases(find_tool("git"), Path(scratch), rerun)
        components: dict[str, object] = {}
        for name, catalog in sorted(review.catalogs.items()):
            definition, kept = review.definitions[name], recorded.get(name, {})
            components[name] = {
                "versions": {
                    version: definition.source.lock(
                        definition, version, kept.get(version), upstream
                    )
                    for version in sorted(catalog.requires)
                }
            }
        if review.python is not None:
            kept = recorded.get(hook_python.COMPONENT, {})
            components[hook_python.COMPONENT] = {
                "versions": {
                    version: hook_python.lock(version, kept.get(version), upstream)
                    for version in sorted(review.python.requires)
                }
            }
    data = canonical({"components": components, "generated_by": LOCK_COMMAND})
    return data, load_policy(lambda name: data if name == LOCK else reader(name))


def lock(root: Path) -> Result:
    """Resolve every reviewed version from upstream and write the Policy Lock.

    A refusal writes nothing, so a rerun converges.
    """
    snapshot = Snapshot(root)
    data, policy = _resolve(snapshot.read, snapshot.read_optional(LOCK), LOCK_COMMAND)
    return Result(
        "policy lock",
        "locked",
        f"{LOCK} holds the facts of every version policy.toml reviews.",
        changed=snapshot.write({LOCK: data}),
        notes=(_note(policy),),
    )


def _parse_request(
    review: Review, tool: str, requested: str
) -> tuple[Catalog | None, str]:
    """Return tool's catalog and the exact version requested, a stable release.

    A policy without a Hook Python has no catalog of it, so its catalog is
    None.
    """
    catalog = review.components.get(tool)
    if catalog is None and tool != hook_python.COMPONENT:
        raise GatesealError(
            "unknown_component",
            f"{POLICY} has no component {tool}; it lists "
            f"{', '.join(sorted(review.catalogs))}, and "
            f"{hook_python.COMPONENT} pins a Hook Python.",
        )
    try:
        return catalog, stable_version(requested, "version")
    except GatesealError:
        raise GatesealError(
            "invalid_invocation",
            f"Name an exact {tool} release as major.minor.patch, such as 1.10.0, "
            f"not {requested!r}.",
        ) from None


def _pin_step(catalog: Catalog | None, tool: str, exact: str) -> tuple[str, ...]:
    """Return the step that makes exact tool's pin, or none when it is.

    catalog is tool's catalog before the add, None when the add started tool
    with exact as its pin.
    """
    if catalog is None or catalog.pin == exact:
        return ()
    return (
        f"Review the diff of {LOCK}. To make {tool} {exact} the pin, run "
        f"gateseal policy pin {tool} {exact} in the policy directory.",
    )


def add(root: Path, tool: str, requested: str) -> Result:
    """Review an exact version of tool, and lock its facts from upstream.

    The version's table goes into policy.toml and its facts into the lock,
    resolved as gateseal policy lock resolves them. Both files are written
    only after every fact checks, policy.toml first, so an interrupted add
    leaves a stale lock that gateseal policy lock completes.
    """
    snapshot = Snapshot(root)
    catalog, exact = _parse_request(review_policy(snapshot.read), tool, requested)
    source = snapshot.read(POLICY)
    new = catalog is None or exact not in catalog.requires
    if catalog is None:
        edited = policy_toml.component(source, tool, exact)
    else:
        edited = policy_toml.reviewed(source, tool, exact) if new else source
    command = f"gateseal policy add {tool} {exact}"
    try:
        data, policy = _resolve(
            lambda name: edited if name == POLICY else snapshot.read(name),
            snapshot.read_optional(LOCK),
            command,
        )
    except MissingUpstream as exc:
        if not new or (exc.name, exc.version) != (tool, exact):
            raise
        raise GatesealError(
            exc.code,
            str(exc),
            next_step=f"Check that {tool} {exact} is released, then run "
            f"{command} again.",
        ) from None
    return Result(
        "policy add",
        "added",
        f"{POLICY} reviews {tool} {exact}, and {LOCK} holds its facts.",
        changed=snapshot.write({POLICY: edited, LOCK: data}),
        next_steps=_pin_step(catalog, tool, exact),
        notes=(_note(policy),),
    )


def pin(root: Path, tool: str, requested: str) -> Result:
    """Make a reviewed version tool's pin, which every pin's requires must allow.

    The previous pin stays reviewed, so a repository may keep it through a
    Version Exception. Only policy.toml changes, since the lock records no pin.
    """
    snapshot = Snapshot(root)
    review = review_policy(snapshot.read)
    catalog, exact = _parse_request(review, tool, requested)
    if catalog is None or exact not in catalog.requires:
        raise GatesealError(
            "version_unreviewed",
            f"{POLICY} does not review {tool} {exact}, so it cannot be the pin.",
            next_step=f"Run gateseal policy add {tool} {exact} in the policy "
            "directory first.",
        )
    pins = {
        name: Release(other.pin, other.requires[other.pin])
        for name, other in review.components.items()
    }
    require_compatible(
        pins | {tool: Release(exact, catalog.requires[exact])},
        "requires_conflict",
        "pins",
        next_step="Move the other pin first, or change requires in policy.toml.",
    )
    source, moved = snapshot.read(POLICY), catalog.pin != exact
    edited = policy_toml.pinned(source, tool, exact) if moved else source
    policy = load_policy(lambda name: edited if name == POLICY else snapshot.read(name))
    kept = (
        f" {catalog.pin} stays reviewed"
        + (
            "."
            if tool == hook_python.COMPONENT
            else ", so a repository may keep it through a Version Exception."
        )
        if moved
        else ""
    )
    return Result(
        "policy pin",
        "pinned",
        f"{POLICY} pins {tool} {exact}.{kept}",
        changed=snapshot.write({POLICY: edited}),
        notes=(_note(policy),),
    )


def check(root: Path) -> Result:
    return Result(
        "policy check",
        "consistent",
        f"policy.toml, its rules and {LOCK} agree.",
        notes=(_note(load_policy(Snapshot(root).read)),),
    )
