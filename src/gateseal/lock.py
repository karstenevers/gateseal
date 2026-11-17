import contextlib
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path
from typing import cast

from gateseal import __version__, github, sources
from gateseal.bootstrap import (
    CONFIG,
    LOCK,
    LOCK_FORMAT,
    MANIFEST,
    sha,
    tracked_bytes,
)
from gateseal.compiler_release import REPOSITORY, GatesealPin, load_runtime
from gateseal.diagnostics import GatesealError, Note
from gateseal.managed_files import (
    make_parents,
    read,
    read_optional,
    remove,
    write_atomic,
)
from gateseal.manifest import (
    CI,
    BundledSource,
    GitSource,
    Manifest,
    Selection,
    Source,
    capabilities,
    git_source,
    notes,
    parse_manifest,
    render,
    resolve,
    tool_note,
)
from gateseal.policy import Policy, load_bundled_policy
from gateseal.tools.model import OWNED, LockSection, Tool, owned_outputs
from gateseal.tools.release_binary import InstalledProgram, checked_programs
from gateseal.validation import (
    canonical,
    decode_json,
    digest,
    fields,
    mapping,
    string,
)


@dataclass(frozen=True)
class Snapshot:
    manifest: bytes
    lock: bytes
    outputs: dict[str, bytes]


@dataclass(frozen=True)
class Locked:
    root: Path
    policy: Policy
    pin: GatesealPin
    snapshot: Snapshot
    source: Source
    selection: Selection


def exclusion(paths: tuple[str, ...]) -> str:
    """Return pre-commit's exclude pattern for paths and everything under them.

    A path choice holds only letters, digits, ., _, - and /, of which only .
    has a meaning in a pattern.
    """
    escaped = (path.replace(".", "\\.") for path in paths)
    return "^(?:" + "|".join(path + "(?:/|$)" for path in escaped) + ")"


def _exclude(selection: Selection, tool: Tool) -> str | None:
    paths = selection.choices.get(tool.use.capability, {}).get("exclude", ())
    return exclusion(paths) if isinstance(paths, tuple) and paths else None


def compile_outputs(
    selection: Selection, pin: GatesealPin, source: Source
) -> dict[str, bytes]:
    """Return every owned output of a selection, by name in Lock order.

    The native configuration starts with gateseal's own repository at the
    pinned commit, whose hook checks the staged files against the Lock before
    any tool runs. Each tool renders its repository after it, in
    capability order, and declares its own outputs, which its capability's
    choices may change. A capability's exclude choice applies to the hooks of
    every tool the policy puts in it. Only hygiene defines one, so a file is
    kept from Ruff or a secret scanner only by a policy that puts that tool in
    hygiene. A Manifest's [ci] adds the provider's workflow, which names
    the gateseal commit CI installs and the policy repository its credential
    helper serves.
    """
    config = {
        "minimum_pre_commit_version": load_runtime().pre_commit,
        "repos": [
            pin.hook_repository(),
            *(tool.entry(_exclude(selection, tool)) for tool in selection.tools),
        ],
    }
    outputs = {CONFIG: canonical(config)}
    for tool in selection.tools:
        outputs |= owned_outputs(tool, selection.choices.get(tool.use.capability, {}))
    if selection.ci is not None:
        outputs[github.WORKFLOW] = github.workflow(selection.ci, pin, source)
    return dict(sorted(outputs.items()))


def policy_record(source: Source, policy: Policy) -> dict[str, object]:
    """Return the Lock's identity of the selected policy release.

    A bundled release is identified by its digest within the gateseal that the
    Lock pins, a Git release by its repository, directory, commit and digest.
    The tag is the label that adoption checked.
    """
    if isinstance(source, BundledSource):
        return {
            "kind": "bundled",
            "release": policy.release,
            "digest": policy.content_digest,
        }
    return {
        "kind": "git",
        "repository": source.repository,
        "path": source.path,
        "commit": source.commit,
        "tag": source.tag,
        "release": policy.release,
        "digest": policy.content_digest,
    }


def compile_lock(
    manifest: bytes,
    source: Source,
    policy: Policy,
    pin: GatesealPin,
    selection: Selection,
) -> dict[str, object]:
    records: dict[LockSection, dict[str, object]] = {
        "components": {},
        "native_artifacts": {},
    }
    for tool in selection.tools:
        section, key, record = tool.lock_entry()
        records[section][key] = record
    inputs: dict[str, object] = {
        "schema": LOCK_FORMAT,
        "manifest_sha256": sha(manifest),
        "gateseal": pin.document(),
        "policy": policy_record(source, policy),
        "components": records["components"],
        "native_artifacts": records["native_artifacts"],
        "choices": selection.choices,
        "exceptions": {
            name: exception.document()
            for name, exception in selection.exceptions.items()
        },
    }
    fingerprint = sha(b"gateseal-inputs-v1\0" + canonical(inputs))
    return {
        **inputs,
        "inputs_fingerprint": fingerprint,
        "outputs": {
            name: {"sha256": sha(content), "encoding": "utf-8", "executable": False}
            for name, content in compile_outputs(selection, pin, source).items()
        },
    }


LOCK_FIELDS = {
    "schema",
    "manifest_sha256",
    "gateseal",
    "policy",
    "components",
    "native_artifacts",
    "choices",
    "exceptions",
    "outputs",
    "inputs_fingerprint",
}


def _records_release(record: object, source: GitSource) -> bool:
    """Return whether a Lock's policy record names source's Git release."""
    if not isinstance(record, dict):
        return False  # pytestmut: skip[return.none] -- every caller negates it
    entry = cast(dict[str, object], record)
    return entry.get("kind") == "git" and all(
        entry.get(key) == value for key, value in asdict(source).items()
    )


def _bundled(source: BundledSource) -> Policy:
    policy = load_bundled_policy()
    if source.release != policy.release:
        raise GatesealError(
            "selected_compiler_required",
            f"The Manifest selects bundled policy {source.release}; gateseal "
            f"{__version__} bundles {policy.release}.",
            next_step="Run the compiler that bundles the selected policy.",
        )
    return policy


def _locked_digest(record: object) -> str:
    entry = mapping(record, "lock.policy")
    found = string(entry.get("digest"), "lock.policy.digest")
    digest(found.removeprefix("sha256:"), "lock.policy.digest")
    return found


def _other_content(source: GitSource, policy: Policy, locked: str) -> GatesealError:
    return GatesealError(
        "lock_mismatch",
        f"Commit {source.commit} holds policy content {policy.content_digest}, "
        f"but {LOCK} records {locked}.",
        next_step="gateseal configure",
    )


def _policy(manifest: Manifest, recorded: object, preparation: Path) -> Policy:
    """Return the release the Manifest selects, without fetching anything.

    A Git release comes from the worktree's store, by the commit and directory
    the Manifest and Lock both name and the digest the Lock records.
    """
    source = manifest.source
    if isinstance(source, BundledSource):
        return _bundled(source)
    if not _records_release(recorded, source):
        raise GatesealError(
            "lock_mismatch",
            "The Lock records another policy release than the Manifest selects.",
            next_step="gateseal configure",
        )
    locked = _locked_digest(recorded)
    policy = sources.stored(preparation, source, locked)
    if policy is not None:
        return policy
    other = sources.stored_any(preparation, source)
    if other is not None:
        raise _other_content(source, other, locked)
    raise GatesealError(
        "policy_unavailable",
        f"Policy commit {source.commit} is not stored in this worktree yet.",
        3,
        "gateseal check",
    )


def acquire(
    manifest: Manifest, recorded: object, preparation: Path, git: str
) -> tuple[Policy, tuple[Note, ...]]:
    """Return the selected release, fetching it if this worktree lacks it.

    Adoption, a Git release the Lock does not record yet, first checks that
    the tag names the commit. The digest is the content's, whatever the Lock
    records, so the caller derives the Lock from it. Returns the policy and a
    note on the tag check.
    """
    source = manifest.source
    if isinstance(source, BundledSource):
        return _bundled(source), ()
    adopting = not _records_release(recorded, source)
    try:
        policy = sources.stored_any(preparation, source)
    except GatesealError:
        policy = None
    if policy is not None and not adopting:
        return policy, ()
    with sources.remote(git, preparation, source.repository) as origin:
        checks = (
            (Note("info", "policy_tag", origin.verify_tag(source)),) if adopting else ()
        )
        return policy or origin.fetch(source), checks


def describe(locked: Locked, today: date | None = None) -> tuple[Note, ...]:
    """Name the policy release, gateseal, tools, choices and exceptions."""
    source, policy, pin = locked.source, locked.policy, locked.pin
    if isinstance(source, BundledSource):
        origin = f"bundled with gateseal {pin.version}"
    else:
        origin = (
            f"from {source.repository} at commit {source.commit} (tag "
            f"{source.tag}, checked at adoption), path {source.path}"
        )
    identity = Note(
        "info",
        "policy",
        f"Policy {policy.release} {origin}, digest {policy.content_digest}.",
    )
    release = Note(
        "info",
        "gateseal",
        f"gateseal {pin.version} from {pin.repository} at commit {pin.commit}, "
        f"with pre-commit {load_runtime().pre_commit}.",
    )
    return (
        identity,
        release,
        *map(tool_note, locked.selection.tools),
        *notes(locked.selection, today or date.today()),
    )


def review_steps(found: tuple[Note, ...]) -> tuple[str, ...]:
    """Name the step a warning about a Version Exception asks for."""
    if not any(note.severity == "warning" for note in found):
        return ()
    return (
        f"Review each version exception a warning names in {MANIFEST}: renew its "
        "review_by, select another reviewed version, or remove it. Then run "
        "gateseal configure.",
    )


def ledger_pin(lock: dict[str, object]) -> GatesealPin:
    """Return the gateseal that a Lock of this format pins.

    The format and the pin belong to the ownership ledger that every release
    reads, so a Lock of another format is refused before any other field.
    gateseal 0.1.0 wrote format 1, which no later release reads.
    """
    found = lock.get("schema")
    if type(found) is not int or found != LOCK_FORMAT:
        # pytestmut: off[cmp.boundary] -- LOCK_FORMAT itself never gets here
        newer = type(found) is int and found > LOCK_FORMAT
        # pytestmut: on
        raise GatesealError(
            "unsupported_schema",
            f"{LOCK} uses Lock format {found!r}, and gateseal {__version__} reads "
            f"only format {LOCK_FORMAT}.",
            next_step=f"Install the newer gateseal that wrote {LOCK}."
            if newer
            else f"Delete {LOCK} and the files gateseal wrote ({CONFIG}, "
            f"{OWNED}/ and {github.WORKFLOW}), then run gateseal configure.",
        )
    try:
        return GatesealPin.parse(lock.get("gateseal"))
    except GatesealError as exc:
        raise _unreadable(exc) from exc


def _locked_pin(lock: dict[str, object]) -> GatesealPin:
    """Return the Lock's gateseal, which must be this one.

    Commands read it first, so another release's Lock names that release
    rather than a field or a policy release this one cannot read.
    """
    pin = ledger_pin(lock)
    if not pin.running:
        raise _other_gateseal(pin)
    return pin


def _other_gateseal(pin: GatesealPin) -> GatesealError:
    return GatesealError(
        "selected_compiler_required",
        f"{LOCK} pins gateseal {pin.version} from {pin.repository} at commit "
        f"{pin.commit}, not this gateseal {__version__}.",
        next_step=f"Install gateseal {pin.version}, or run gateseal upgrade to "
        f"move {LOCK} to gateseal {__version__}.",
    )


@dataclass(frozen=True)
class _LockInputs:
    """A Lock whose own fields are checked, and what its Manifest selects."""

    document: dict[str, object]
    pin: GatesealPin
    manifest: Manifest
    policy: Policy
    selection: Selection


def _lock_inputs(manifest: bytes, lock: bytes, preparation: Path) -> _LockInputs:
    """Read a Lock and its Manifest, and the policy release they select."""
    document = decode_json(lock)
    pin = _locked_pin(document)
    document = fields(document, LOCK_FIELDS, "lock")
    parsed = parse_manifest(manifest)
    policy = _policy(parsed, document["policy"], preparation)
    return _LockInputs(document, pin, parsed, policy, resolve(parsed, policy))


def tracked(root: Path, name: str) -> bytes:
    return tracked_bytes(read(root, name, non_executable=True))


def _recorded(lock: dict[str, object], name: str, data: bytes) -> bool:
    """Return whether lock records data as its Manifest or as the output name."""
    if name == MANIFEST:
        return lock.get("manifest_sha256") == sha(data)
    outputs = lock.get("outputs")
    if not isinstance(outputs, dict):
        return False  # pytestmut: skip[return.none] -- every caller negates it
    entry = cast(dict[str, object], outputs).get(name)
    return isinstance(entry, dict) and cast(dict[str, object], entry).get(
        "sha256"
    ) == sha(data)


@dataclass(frozen=True)
class Ledger:
    """What the commit check reads of a Lock that this gateseal wrote.

    files holds the Manifest's SHA-256 and each owned output's, by path, from
    the ownership ledger. programs holds each executable checked at commit
    on the machine's platform.
    """

    files: dict[str, str]
    programs: tuple[InstalledProgram, ...]


def own_ledger(lock: bytes, platform: str | None) -> Ledger:
    """Return what the commit check reads of a Lock that this gateseal wrote.

    Reading it needs no policy and no network. A path this gateseal never
    writes means the Lock was edited, as it does for _obsolete. platform is
    the machine's mise platform, or None on a machine gateseal does not
    support, where no executable is checked.
    """
    document = _readable(lock)
    _locked_pin(document)
    try:
        manifest = digest(document.get("manifest_sha256"), "manifest_sha256")
        outputs = {
            name: digest(
                mapping(record, f"outputs.{name}").get("sha256"),
                f"outputs.{name}.sha256",
            )
            for name, record in mapping(document.get("outputs"), "outputs").items()
        }
        native = mapping(document.get("native_artifacts"), "native_artifacts")
        programs = () if platform is None else checked_programs(native, platform)
    except GatesealError as exc:
        raise _unreadable(exc) from exc
    for name in outputs:
        if not _ownable(name):
            raise GatesealError(
                "ownership_conflict",
                f"{LOCK} lists {name} as a file gateseal wrote, but gateseal "
                f"{__version__} never writes it.",
                next_step=f"Restore {LOCK} from Git, then run gateseal configure.",
            )
    return Ledger({MANIFEST: manifest, **outputs}, programs)


def _optional(root: Path, name: str) -> bytes | None:
    data = read_optional(root, name, non_executable=True)
    return None if data is None else tracked_bytes(data)


def _recorded_lock(data: bytes | None) -> dict[str, object]:
    """Return a worktree Lock's fields, or nothing when it is not a JSON object."""
    if data is None:
        return {}
    try:
        return decode_json(data)
    except GatesealError:
        return {}


def ensure_policy(root: Path, preparation: Path, git: str) -> None:
    """Store the Git release the worktree's Lock records, if it is not stored.

    Every command but status runs this, so a fresh clone or worktree
    acquires its policy without configure. Only the locked commit is fetched,
    and its content must have the digest the Lock records. A Lock that names
    another release than the Manifest is left for load to report.
    """
    data = _optional(root, MANIFEST)
    if data is None:
        return
    manifest = parse_manifest(data)
    source = manifest.source
    document = _recorded_lock(_optional(root, LOCK))
    recorded = document.get("policy")
    if not isinstance(source, GitSource) or not _records_release(recorded, source):
        return
    _locked_pin(document)
    locked = _locked_digest(recorded)
    with contextlib.suppress(GatesealError):
        if sources.stored(preparation, source, locked) is not None:
            return
    policy, _ = acquire(manifest, recorded, preparation, git)
    if policy.content_digest != locked:
        raise _other_content(source, policy, locked)
    sources.store(preparation, source, policy)


def load(root: Path, preparation: Path, *, outputs: bool = True) -> Locked:
    """Return the worktree's checked Manifest, Lock and owned outputs.

    outputs False leaves the outputs unread: the Manifest and Lock must agree,
    and the snapshot holds the outputs they compile to. An upgrade reads that,
    since an output may hold what an interrupted upgrade wrote, and
    owned_changes decides what it may replace.
    """
    try:
        manifest = tracked(root, MANIFEST)
        lock = tracked(root, LOCK)
        inputs = _lock_inputs(manifest, lock, preparation)
        source, policy = inputs.manifest.source, inputs.policy
        pin, selection = inputs.pin, inputs.selection
        compiled = compile_outputs(selection, pin, source)
        found = {
            name: tracked(root, name) if outputs else content
            for name, content in compiled.items()
        }
        expected = compile_lock(manifest, source, policy, pin, selection)
        if canonical(inputs.document) != canonical(expected):
            raise GatesealError(
                "lock_mismatch",
                "Lock inputs or ownership disagree with the selected policy.",
                next_step="gateseal configure",
            )
        for name, content in compiled.items():
            if found[name] != content:
                raise GatesealError(
                    "ownership_conflict",
                    f"Restore the owned {name}; it has been edited.",
                )
        snapshot = Snapshot(manifest, lock, found)
        return Locked(root, policy, pin, snapshot, source, selection)
    except FileNotFoundError as exc:
        raise GatesealError(
            "missing_configuration", "Run gateseal configure before this command."
        ) from exc


@dataclass(frozen=True)
class PolicyRequest:
    """configure's policy flags. Each one supplied asserts the Manifest's value."""

    source: str | None = None
    commit: str | None = None
    path: str | None = None
    tag: str | None = None

    def build(self, git: str, preparation: Path) -> Source:
        """Return the source a new Manifest selects.

        A Git policy named by its tag alone selects the commit the tag names
        now, which adoption then reviews like any other.
        """
        if self.source in (None, "bundled"):
            if any(v is not None for v in (self.commit, self.path, self.tag)):
                raise GatesealError(
                    "invalid_invocation",
                    "--policy-commit, --policy-path, and --policy-tag select a Git "
                    "policy; pass its repository with --policy.",
                )
            return BundledSource(load_bundled_policy().release)
        if self.tag is None:
            raise GatesealError(
                "invalid_invocation",
                "A Git policy needs --policy-tag with the release tag to adopt.",
            )
        # A placeholder commit lets git_source check the other fields before
        # the tag names the commit.
        source = git_source(
            self.source, self.path or ".", self.commit or "0" * 40, self.tag
        )
        if self.commit is not None:
            return source
        with sources.remote(git, preparation, source.repository) as origin:
            return replace(source, commit=origin.tagged(source.tag))

    def target(self, current: Source, git: str, preparation: Path) -> Source:
        """Return the source an upgrade moves the Manifest to.

        bundled selects this compiler's bundled release. A Git target keeps the
        current repository and directory unless a flag names others, and needs
        the tag it adopts.
        """
        if self.source == "bundled":
            return PolicyRequest("bundled", self.commit, self.path, self.tag).build(
                git, preparation
            )
        known = current if isinstance(current, GitSource) else None
        repository = self.source or (known and known.repository)
        if repository is None:
            raise GatesealError(
                "invalid_invocation",
                "The bundled policy has no repository; name the Git policy to "
                "adopt with --policy and --policy-tag.",
            )
        path = self.path or _kept_path(known, repository)
        return PolicyRequest(repository, self.commit, path, self.tag).build(
            git, preparation
        )

    def check(self, source: Source) -> None:
        """Fail unless every supplied flag equals the Manifest's source."""
        expected: dict[str, str] = (
            {"source": "bundled"}
            if isinstance(source, BundledSource)
            else {
                "source": source.repository,
                "commit": source.commit,
                "path": source.path,
                "tag": source.tag,
            }
        )
        given = {
            "source": self.source,
            "commit": self.commit,
            "path": self.path,
            "tag": self.tag,
        }
        wrong = [
            key
            for key, value in given.items()
            if value is not None and value != expected.get(key)
        ]
        if wrong:
            raise GatesealError(
                "configure_assertion_failed",
                f"{MANIFEST} selects another policy than "
                + ", ".join(
                    "--policy" + ("" if k == "source" else "-" + k) for k in wrong
                )
                + ".",
                next_step=f"Edit [policy] in {MANIFEST}, then run gateseal configure.",
            )


def _kept_path(known: GitSource | None, repository: str) -> str:
    """Return the current Git policy's directory if it is in repository, else '.'."""
    return known.path if known is not None and known.repository == repository else "."


@dataclass(frozen=True)
class Changes:
    """The tracked files a plan writes, and the outputs it deletes, by name."""

    writes: dict[str, bytes]
    removes: tuple[str, ...]

    @property
    def names(self) -> tuple[str, ...]:
        return (*self.writes, *self.removes)


def owned_changes(
    root: Path, planned: dict[str, bytes], recorded: dict[str, object]
) -> Changes:
    """Return what the worktree needs to hold planned, checking ownership first.

    The Lock is fully derived, so it is replaced whenever it differs. Any
    other file is replaced only when the recorded Lock holds its bytes, so a
    changed selection replaces what gateseal wrote and nothing else, and an
    upgrade edits only the Manifest that the Lock was compiled from. An output
    the plan drops is deleted only while it holds the recorded bytes.
    """
    removes = _obsolete(root, recorded, planned)
    writes: dict[str, bytes] = {}
    for name, content in planned.items():
        data = _optional(root, name)
        if data == content:
            continue
        if data is not None and name != LOCK and not _recorded(recorded, name, data):
            raise GatesealError(
                "ownership_conflict",
                f"Existing {name} differs from the generated file; "
                "preserve or move it.",
            )
        writes[name] = content
    return Changes(writes, removes)


def write_tracked(root: Path, changes: Changes) -> None:
    """Write the outputs, delete the dropped ones, then write the Manifest and Lock.

    The Lock goes last: until it is written, the old Lock still records the
    outputs it owned, so an interrupted change of selection can be retried.
    """
    last = (MANIFEST, LOCK)
    for name in changes.writes:
        make_parents(root, name)
    for name in (n for n in changes.writes if n not in last):
        write_atomic(root / name, changes.writes[name], 0o644)
    for name in changes.removes:
        remove(root, name)
    for name in last:
        if name in changes.writes:
            write_atomic(root / name, changes.writes[name], 0o644)


def configure(
    root: Path,
    git: str,
    preparation: Path,
    capability: list[str] | None,
    request: PolicyRequest,
    ci: str | None,
) -> tuple[tuple[str, ...], tuple[Note, ...]]:
    """Converge the tracked files on the selected policy.

    Returns the files written or deleted and notes on the adoption checks,
    choices and exceptions. The Lock is overwritten when it differs, because it is fully
    derived. An output is overwritten only when its bytes are those the
    existing Lock records, so a changed selection replaces what gateseal wrote
    and nothing else, and an output the selection drops is deleted only when it
    holds those bytes. Every file, the fetched release, every choice, exception
    and requirement are checked before the first write, so a rerun after an
    interruption converges.
    """
    manifest = _optional(root, MANIFEST)
    if manifest is None:
        manifest = _new_manifest(capability, request, git, preparation, ci)
    parsed = parse_manifest(manifest)
    _assert_flags(parsed, capability, request, ci)
    previous = _optional(root, LOCK)
    if previous is None:
        recorded: dict[str, object] = {}
        pin = release_pin(git, preparation)
    else:
        recorded = _readable(previous)
        pin = _locked_pin(recorded)
    policy, checks = acquire(parsed, recorded.get("policy"), preparation, git)
    selection = resolve(parsed, policy)
    outputs = compile_outputs(selection, pin, parsed.source)
    document = compile_lock(manifest, parsed.source, policy, pin, selection)
    if _other_build(recorded, document, parsed.source):
        raise GatesealError(
            "selected_compiler_required",
            f"{LOCK} records a bundled policy that this build of gateseal "
            f"{__version__} does not bundle.",
            next_step=f"Install gateseal from commit {pin.commit} of "
            f"{pin.repository}, the build that {LOCK} pins.",
        )
    planned = {MANIFEST: manifest, LOCK: canonical(document), **outputs}
    changes = owned_changes(root, planned, recorded)
    if isinstance(parsed.source, GitSource):
        sources.store(preparation, parsed.source, policy)
    write_tracked(root, changes)
    locked = Locked(
        root,
        policy,
        pin,
        Snapshot(manifest, canonical(document), outputs),
        parsed.source,
        selection,
    )
    return changes.names, (*checks, *describe(locked))


def _new_manifest(
    capability: list[str] | None,
    request: PolicyRequest,
    git: str,
    preparation: Path,
    ci: str | None,
) -> bytes:
    if capability is None:
        raise GatesealError(
            "missing_capability",
            "Select at least one capability with configure --capability, such "
            "as hygiene for file checks, python to check Python with Ruff and "
            "secrets to detect secrets.",
        )
    return render(
        capabilities(list(set(capability))),
        request.build(git, preparation),
        None if ci is None else CI(ci, "main"),
    )


def _assert_flags(
    parsed: Manifest,
    capability: list[str] | None,
    request: PolicyRequest,
    ci: str | None,
) -> None:
    """Fail on the first flag that selects other than the Manifest does."""
    if capability is not None and set(capability) != set(parsed.capabilities):
        raise GatesealError(
            "configure_assertion_failed",
            f"{MANIFEST} selects {', '.join(parsed.capabilities)}.",
            next_step=f"Edit capabilities in {MANIFEST}, then run gateseal configure.",
        )
    request.check(parsed.source)
    if ci is not None and (parsed.ci is None or parsed.ci.provider != ci):
        raise GatesealError(
            "configure_assertion_failed",
            f"{MANIFEST} selects "
            + ("no CI provider." if parsed.ci is None else f"{parsed.ci.provider}."),
            next_step=f"Edit [ci] in {MANIFEST}, then run gateseal configure.",
        )


def release_pin(git: str, preparation: Path) -> GatesealPin:
    """Return this gateseal's pin: the commit its release tag names.

    The tag comes through the user's Git, as a policy release's does, so Git's
    credentials and url.<base>.insteadOf apply.
    """
    tag = f"v{__version__}"
    try:
        with sources.remote(
            git, preparation, REPOSITORY, subject="gateseal's repository"
        ) as origin:
            commit = origin.peeled(tag)
    except GatesealError as exc:
        raise GatesealError(
            "upstream_unavailable",
            str(exc),
            4,
            f"Check that git ls-remote {REPOSITORY} works, then rerun the command.",
        ) from exc
    if commit is None:
        raise GatesealError(
            "upstream_tag_missing",
            f"gateseal's repository {REPOSITORY} has no tag {tag} for this "
            f"gateseal {__version__}.",
            next_step="Install a released gateseal, then rerun the command.",
        )
    return GatesealPin(REPOSITORY, __version__, commit)


def _readable(previous: bytes) -> dict[str, object]:
    """Return the existing Lock's fields, naming the way out when it is not JSON."""
    try:
        return decode_json(previous)
    except GatesealError as exc:
        raise _unreadable(exc) from exc


def _unreadable(exc: GatesealError) -> GatesealError:
    return GatesealError(
        exc.code,
        f"Cannot read {LOCK}: {exc}",
        next_step=f"Restore {LOCK} from Git, or delete it and the files gateseal "
        "wrote, then run gateseal configure.",
    )


def _other_build(
    recorded: dict[str, object], document: dict[str, object], source: Source
) -> bool:
    """Tell whether another build of this version wrote the existing Lock.

    Two builds of one version can bundle different policy bytes.
    """
    kept = recorded.get("policy")
    return (
        isinstance(kept, dict)
        and cast(dict[str, object], kept).get("kind") == "bundled"
        and isinstance(source, BundledSource)
        and kept != document["policy"]
    )


def _obsolete(
    root: Path, recorded: dict[str, object], planned: dict[str, bytes]
) -> tuple[str, ...]:
    """Return the outputs the old Lock owns and the new selection drops.

    Each still holds the bytes that Lock records, so configure can delete it, as
    for the workflow that a removed [ci] would otherwise leave running. An
    edited one stops configure, as an edited output it writes does. A name this
    gateseal could never own stops it too, so an edited Lock, or one that
    another release wrote, cannot have it delete another file.
    """
    owned = recorded.get("outputs")
    names = [
        name
        for name in (cast(dict[str, object], owned) if isinstance(owned, dict) else {})
        if name not in planned
    ]
    obsolete: list[str] = []
    for name in sorted(names):
        if not _ownable(name):
            raise GatesealError(
                "ownership_conflict",
                f"{LOCK} lists {name} as a file gateseal wrote, but gateseal "
                f"{__version__} never writes it.",
                next_step=f"If a gateseal upgrade wrote {LOCK}, undo it with git "
                "revert. Otherwise restore it from Git.",
            )
        data = _optional(root, name)
        if data is None:
            continue
        if not _recorded(recorded, name, data):
            raise GatesealError(
                "ownership_conflict",
                f"The selection no longer writes {name}, and it differs from the "
                "file gateseal wrote.",
                next_step=f"Delete {name}, or move it aside to keep it, then run "
                "gateseal configure.",
            )
        obsolete.append(name)
    return tuple(obsolete)


def _ownable(name: str) -> bool:
    return name == CONFIG or name == github.WORKFLOW or name.startswith(OWNED + "/")
