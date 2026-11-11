"""Upgrade or roll back one repository's reviewed inputs: gateseal upgrade.

One planner moves the policy release, a component's version and the compiler.
It starts from a worktree whose Manifest and Lock agree, plans every target,
and renders the result with the compiler that the result pins: this one, or
the candidate that a compiler target installs. Every target and every
requirement between components is checked before the first write, and the
writes follow configure's ownership rules. Activation runs only when asked,
after the tracked files are written, so a failed activation keeps them.
"""

import contextlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from gateseal import (
    bootstrap,
    candidate,
    hook_python,
    lock,
    manifest_toml,
    runtime,
    sources,
)
from gateseal.bootstrap import LOCK, MANIFEST
from gateseal.compiler_release import CompilerArtifact, artifact_from_flags
from gateseal.diagnostics import GatesealError, Note, Result
from gateseal.manifest import (
    BundledSource,
    ExceptionRequest,
    GitSource,
    Manifest,
    Selection,
    parse_manifest,
    resolve,
)
from gateseal.policy import Policy
from gateseal.tools.model import NAME, Tool
from gateseal.validation import (
    canonical,
    decode_json,
    mapping,
    sequence,
    version,
    version_key,
)

ACTIVATE = "gateseal activate"

RESTORE = (
    "Run gateseal configure after an edit of gateseal.toml. If an interrupted "
    "gateseal upgrade left gateseal.toml ahead of gateseal.lock, restore "
    "gateseal.toml and the outputs it changed with git restore, then run the "
    "upgrade again."
)

RESOLVE = (
    "Name each such component in the same gateseal upgrade: COMPONENT follows "
    "the policy's pin, and COMPONENT=VERSION with --reason and --review-by "
    "selects another version the policy reviewed."
)


@dataclass(frozen=True)
class Request:
    """What gateseal upgrade's arguments ask for.

    policy holds the --policy flags, and compiler the --compiler flags, when
    any is given. components maps each named component to its exact target, or
    to None for the policy's pin. reason and review_by fill each Version
    Exception a target needs.
    """

    policy: lock.PolicyRequest | None
    compiler: tuple[str | None, str | None, str | None] | None
    components: Mapping[str, str | None]
    reason: str | None
    review_by: date | None
    activate: bool


def components(arguments: list[str]) -> dict[str, str | None]:
    """Parse COMPONENT and COMPONENT=VERSION targets, each component once."""
    found: dict[str, str | None] = {}
    for argument in arguments:
        name, separator, target = argument.partition("=")
        if not NAME.fullmatch(name) or name in found:
            raise GatesealError(
                "invalid_invocation",
                f"Name each component once, as betterleaks or betterleaks=1.9.0, "
                f"not {argument!r}.",
            )
        found[name] = version(target, f"{name}'s target") if separator else None
    return found


@dataclass(frozen=True)
class Plan:
    """The tracked files an upgrade writes, before any is written.

    files holds the Manifest, the Lock and each output. preserved names the
    components kept at a version newer than the policy's pin. store is the Git
    release to store before writing, as configure stores the one it adopts.
    """

    files: dict[str, bytes]
    notes: tuple[Note, ...]
    preserved: frozenset[str] = frozenset()
    store: tuple[GitSource, Policy] | None = None


def _unknown(name: str, policy: Policy) -> GatesealError:
    if name == "pre-commit":
        return GatesealError(
            "runtime_target",
            "pre-commit is part of the Compiler Runtime, so it moves only with the "
            "compiler.",
            next_step="Upgrade the compiler to the release that carries the "
            "pre-commit you need, with --compiler-source or --compiler-wheel and "
            "--compiler-sha256.",
        )
    if name == hook_python.COMPONENT:
        return GatesealError(
            "runtime_target",
            "python is the Compiler Runtime's CPython, which moves only with the "
            "compiler. A policy's Hook Python moves only with the policy release.",
            next_step="Upgrade the compiler, or the policy to a release that pins "
            "the Hook Python you need.",
        )
    return GatesealError(
        "unsupported_component",
        f"{name} is not a component the selected capabilities use."
        if name in policy.tools
        else f"The policy offers no component {name}.",
    )


def _exception(
    request: Request, name: str, target: str, kept: ExceptionRequest | None, why: str
) -> ExceptionRequest:
    """Return the Version Exception that selects target, with its reason and date.

    A flag replaces the kept exception's reason or date when it selects the
    same version; another version needs both flags.
    """
    kept_reason, kept_review_by = (
        (kept.reason, kept.review_by)
        if kept is not None and kept.version == target
        else (None, None)
    )
    reason = request.reason or kept_reason
    review_by = request.review_by or kept_review_by
    if not reason or review_by is None:
        raise GatesealError(
            "invalid_exception",
            f"{why}, so it needs a Version Exception with a reason and a review date.",
            next_step="Add --reason and --review-by, or name the component without "
            "a version to follow the policy's pin.",
        )
    return ExceptionRequest(name, target, reason, review_by)


def _exception_changes(
    manifest: Manifest, current: lock.Locked, policy: Policy, request: Request
) -> tuple[dict[str, ExceptionRequest | None], frozenset[str]]:
    """Return each component target's exception, None to remove one, and what stays.

    A target without a version follows the policy's pin, but keeps a newer
    version that the policy still reviews, as a Version Exception when the
    pin no longer selects it.
    """
    used = {
        name: tool
        for name, tool in policy.tools.items()
        if tool.use.capability in manifest.capabilities
    }
    selected = {tool.definition.name: tool.version for tool in current.selection.tools}
    existing = {item.component: item for item in manifest.exceptions}
    changes: dict[str, ExceptionRequest | None] = {}
    preserved: set[str] = set()
    for name, target in request.components.items():
        tool = used.get(name)
        if tool is None:
            raise _unknown(name, policy)
        kept = existing.get(name)
        # A tool that the new policy release adds has no current version.
        now = selected.get(name, tool.pin)
        newer = version_key(now) > version_key(tool.pin) and now in tool.versions
        if target is None and newer:
            preserved.add(name)
            changes.update(_keep_newer(request, name, now, tool.pin, kept))
        else:
            changes.update(
                _target_change(request, name, target or tool.pin, tool, kept)
            )
    _reject_unused_flags(request, changes)
    return changes, frozenset(preserved)


def _keep_newer(
    request: Request, name: str, now: str, pin: str, kept: ExceptionRequest | None
) -> dict[str, ExceptionRequest | None]:
    """Return the exception that keeps name at now, newer than pin, if one is due."""
    if kept is None or request.reason or request.review_by:
        why = f"Keeping {name} {now}, newer than the policy's pin {pin}"
        return {name: _exception(request, name, now, kept, why)}
    return {}


def _target_change(
    request: Request, name: str, target: str, tool: Tool, kept: ExceptionRequest | None
) -> dict[str, ExceptionRequest | None]:
    """Return target's change: none, None to drop the kept exception, or a new one."""
    if target not in tool.versions:
        raise GatesealError(
            "unknown_version",
            f"The policy has not reviewed {name} {target}. Reviewed versions: "
            f"{', '.join(sorted(tool.versions, key=version_key))}.",
        )
    if target == tool.pin:
        return {} if kept is None else {name: None}
    if kept is None or kept.version != target or request.reason or request.review_by:
        why = f"{name} {target} is not the policy's pin {tool.pin}"
        return {name: _exception(request, name, target, kept, why)}
    return {}


def _reject_unused_flags(
    request: Request, changes: dict[str, ExceptionRequest | None]
) -> None:
    if (request.reason or request.review_by) and not any(changes.values()):
        raise GatesealError(
            "invalid_invocation",
            "--reason and --review-by fill a Version Exception, but no target "
            "selects a version other than the policy's pin.",
        )


def _require_reviewed(manifest: Manifest, policy: Policy) -> None:
    """Fail on every Version Exception that the policy does not review, at once."""
    missing = [
        f"{item.component} {item.version}"
        for item in manifest.exceptions
        if (tool := policy.tools.get(item.component)) is not None
        and item.version not in tool.versions
    ]
    if missing:
        raise GatesealError(
            "incompatible_exception",
            "The selected policy does not review the exceptions' "
            f"{', '.join(missing)}.",
            next_step=RESOLVE,
        )


def _plan(
    current: lock.Locked,
    request: Request,
    artifact: CompilerArtifact,
    git: str,
    preparation: Path,
) -> Plan:
    """Plan policy and component targets, and a new locator for this compiler."""
    old = parse_manifest(current.snapshot.manifest)
    source = (
        request.policy.target(current.source, git, preparation)
        if request.policy
        else current.source
    )
    recorded = decode_json(current.snapshot.lock).get("policy")
    policy, checks = lock.acquire(
        replace(old, source=source), recorded, preparation, git
    )
    changes, preserved = _exception_changes(old, current, policy, request)
    manifest = current.snapshot.manifest
    if source != current.source or changes:
        moved = None if source == current.source else source
        manifest = manifest_toml.edited(manifest, moved, changes)
    parsed = parse_manifest(manifest)
    _require_reviewed(parsed, policy)
    selection = _selection(parsed, policy)
    outputs = lock.compile_outputs(selection, artifact, source)
    document = canonical(
        lock.compile_lock(manifest, source, policy, artifact, selection)
    )
    locked = lock.Locked(
        current.root,
        policy,
        artifact,
        lock.Snapshot(manifest, document, outputs),
        source,
        selection,
    )
    return Plan(
        {MANIFEST: manifest, LOCK: document, **outputs},
        (*checks, *lock.describe(locked)),
        preserved,
        (source, policy) if isinstance(source, GitSource) else None,
    )


def _selection(manifest: Manifest, policy: Policy) -> Selection:
    """Resolve manifest, naming RESOLVE when its exceptions break a requirement."""
    try:
        return resolve(manifest, policy)
    except GatesealError as exc:
        if exc.code != "incompatible_exception" or exc.next_step:
            raise
        raise GatesealError(exc.code, str(exc), exc.exit_code, RESOLVE) from exc


def _plan_candidate(
    current: lock.Locked,
    request: Request,
    compiler: candidate.Candidate,
    git: str,
    preparation: Path,
) -> Plan:
    """Plan a compiler target with the candidate, which must take every pin as is.

    A bundled policy belongs to its compiler, so it moves with it only when
    --policy bundled asks for that.
    """
    _require_alone(request)
    moving = request.policy is not None
    manifest = current.snapshot.manifest
    with compiler.scratch(git, preparation) as repository:
        if moving or isinstance(current.source, BundledSource):
            capabilities = parse_manifest(manifest).capabilities
            release, digest = compiler.bundled(repository, capabilities)
            if not moving and (release, digest) != (
                current.policy.release,
                current.policy.content_digest,
            ):
                raise GatesealError(
                    "bundled_policy_moves",
                    f"gateseal {compiler.artifact.version} bundles policy {release} "
                    f"with digest {digest}, not the selected {current.policy.release} "
                    f"with digest {current.policy.content_digest}.",
                    next_step="Add --policy bundled to move to that policy with the "
                    "compiler, or adopt a Git policy release first.",
                )
            if BundledSource(release) != current.source:
                manifest = manifest_toml.edited(manifest, BundledSource(release), {})
        files, notes = compiler.configure(repository, manifest)
    return Plan(files, notes)


def _require_alone(request: Request) -> None:
    """Fail unless the compiler target moves alone, or with --policy bundled."""
    if (
        request.components
        or request.reason
        or request.review_by
        or (
            request.policy is not None
            and request.policy != lock.PolicyRequest("bundled")
        )
    ):
        raise GatesealError(
            "invalid_invocation",
            "A compiler target moves alone, or with --policy bundled. Upgrade the "
            "compiler first, then the policy or components with its command.",
        )


def _state(before: str, after: str) -> str:
    """Name a move between two values that each start with a version.

    It is change when either has no version, or when only what follows the
    version moves, such as a CPython build.
    """
    if before == after:
        return "unchanged"
    try:
        # pytestmut: off[num.shift] -- only the first word is read
        old, new = (version_key(value.split(" ", 1)[0]) for value in (before, after))
        # pytestmut: on
    except GatesealError:
        return "change"
    # pytestmut: off[cmp.boundary] -- equal versions returned above
    return "change" if old == new else "upgrade" if new > old else "rollback"
    # pytestmut: on


HOOK_PYTHON = "hook-python"
"""The Hook Python's target, which differs from the runtime's python."""


def _target_entry(
    target: str, before: str | None, after: str | None, state: str, note: str = ""
) -> dict[str, object]:
    """Describe one target's move; an added one has no before, a removed no after."""
    moved = " to ".join(dict.fromkeys(v for v in (before, after) if v is not None))
    label = "Hook Python" if target == HOOK_PYTHON else target
    return {
        "severity": "info",
        "category": "upgrade",
        "target": target,
        "state": state,
        "from": before,
        "to": after,
        "message": f"{label} {moved}: {state}{note}.",
    }


def _versions(document: dict[str, object]) -> dict[str, str]:
    """Return each component's version that a Lock records, by its target."""
    found: dict[str, str] = {}
    for key, record in mapping(document.get("components"), "components").items():
        name = HOOK_PYTHON if key == hook_python.COMPONENT else key
        found[name] = str(mapping(record, key).get("version"))
    for name, record in mapping(document.get("native_artifacts"), "native").items():
        found[name] = str(mapping(record, name).get("version"))
    return found


def _runtime(document: dict[str, object]) -> dict[str, str]:
    """Return the Python and pre-commit versions of a Lock's Compiler Runtime."""
    record = mapping(document["runtime"], "runtime")
    python = next(iter(mapping(record["python"], "runtime.python").values()))
    parts = mapping(python, "python")
    release = ".".join(str(parts[key]) for key in ("major", "minor", "patch"))
    return {
        "python": f"{release} build {parts['build']}",
        "pre-commit": str(mapping(record["pre_commit"], "pre_commit")["version"]),
    }


def _policy(record: dict[str, object]) -> str:
    if record["kind"] == "bundled":
        return f"bundled {record['release']} ({record['digest']})"
    return f"{record['tag']} at {record['commit']} ({record['digest']})"


def report(
    before: dict[str, object], after: dict[str, object], preserved: frozenset[str]
) -> list[dict[str, object]]:
    """Describe how two Locks differ: one entry per target, then per exception."""
    entries: list[dict[str, object]] = []
    old, new = mapping(before["compiler"], "c"), mapping(after["compiler"], "c")
    state = _state(str(old["version"]), str(new["version"]))
    if state == "unchanged" and old != new:
        state = "change"
    entries.append(
        _target_entry("compiler", str(old["version"]), str(new["version"]), state)
    )
    for name, value in _runtime(after).items():
        previous = _runtime(before)[name]
        entries.append(_target_entry(name, previous, value, _state(previous, value)))
    kept = {
        k: v
        for k, v in mapping(before["policy"], "p").items()
        if k != "compiler_sha256"
    }
    moved = {
        k: v for k, v in mapping(after["policy"], "p").items() if k != "compiler_sha256"
    }
    entries.append(
        _target_entry(
            "policy", _policy(kept), _policy(moved), _state(str(kept), str(moved))
        )
    )
    entries += _component_entries(before, after, preserved)
    entries += _exception_entries(before, after)
    return entries


def _component_entries(
    before: dict[str, object], after: dict[str, object], preserved: frozenset[str]
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    versions, targets = _versions(before), _versions(after)
    pins = mapping(after["exceptions"], "exceptions")
    for name, value in targets.items():
        previous = versions.get(name)
        if previous is None:
            entries.append(_target_entry(name, None, value, "added"))
        elif name in preserved:
            pin = mapping(pins[name], name)["pin"] if name in pins else value
            note = f", newer than the policy's pin {pin}"
            entries.append(_target_entry(name, previous, value, "preserved", note))
        else:
            entries.append(
                _target_entry(name, previous, value, _state(previous, value))
            )
    for name in sorted(versions.keys() - targets.keys()):
        entries.append(_target_entry(name, versions[name], None, "removed"))
    return entries


def _exception_entries(
    before: dict[str, object], after: dict[str, object]
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    pins = mapping(after["exceptions"], "exceptions")
    exceptions = mapping(before["exceptions"], "exceptions")
    for name in sorted(set(exceptions) | set(pins)):
        was, now = exceptions.get(name), pins.get(name)
        record = mapping(now if now is not None else was, name)
        status = (
            "added"
            if was is None
            else "removed"
            if now is None
            else "redundant"
            if record["version"] == record["pin"]
            else "retained"
            if mapping(was, name)["version"] == record["version"]
            else "changed"
        )
        entries.append(
            {
                "severity": "warning" if status == "redundant" else "info",
                "category": "exception",
                "component": name,
                "version": record["version"],
                "status": status,
                "message": f"Version exception {name} {record['version']} (policy "
                f"pin {record['pin']}): {status}.",
            }
        )
    return entries


def _result(before: dict[str, object], plan: Plan, written: tuple[str, ...]) -> Result:
    after = decode_json(plan.files[LOCK])
    entries = report(before, after, plan.preserved)
    steps: list[str] = []
    release = mapping(after["compiler"], "compiler")["version"]
    if release != mapping(before["compiler"], "compiler")["version"]:
        steps.append(
            f"Install the command of gateseal {release} as Install a reviewed wheel "
            "shows; only it runs gateseal commands on the new Lock."
        )
    if written:
        steps += [ACTIVATE, "Review git diff, then stage and commit the changed files."]
    return Result(
        "upgrade",
        "upgraded" if written else "unchanged",
        "Upgraded the tracked files."
        if written
        else "Every target is already selected.",
        changed=written,
        next_steps=(*steps, *lock.review_steps(plan.notes)),
        details="\n".join(str(entry["message"]) for entry in entries),
        notes=plan.notes,
        entries=tuple(entries),
    )


def _activation_failed(
    result: Result, failure: GatesealError, remaining: list[str]
) -> Result:
    """Report a failed activation the way gateseal activate reports it."""
    return replace(
        result,
        code="activation_failed",
        exit_code=failure.exit_code,
        message=f"{result.message} Activation failed with {failure.code}, and the "
        f"tracked files stay upgraded: {failure}",
        next_steps=tuple(dict.fromkeys((failure.next_step or ACTIVATE, *remaining))),
    )


def _activate(
    worktree: runtime.Worktree,
    preparation: Path,
    result: Result,
    compiler: candidate.Candidate | None,
) -> Result:
    """Activate the upgraded Lock with the compiler it pins, keeping the upgrade."""
    remaining = [step for step in result.next_steps if step != ACTIVATE]
    try:
        if compiler is not None:
            done = compiler.run(worktree.root, "activate")
            changed = tuple(map(str, sequence(done.get("changed", []), "changed")))
            steps = tuple(map(str, sequence(done.get("next_steps", []), "steps")))
        else:
            with runtime.operation_lock(preparation):
                locked = lock.load(worktree.root, preparation)
                activated = runtime.activate(worktree, locked, None)
            changed, steps = activated.changed, activated.next_steps
    except GatesealError as exc:
        return _activation_failed(result, exc, remaining)
    except bootstrap.BootstrapError as exc:
        failure = GatesealError("needs_activation", str(exc), 3, exc.next_step)
        return _activation_failed(result, failure, remaining)
    except OSError as exc:
        failure = GatesealError("filesystem_error", str(exc), 4)
        return _activation_failed(result, failure, remaining)
    return replace(
        result,
        message=result.message + " Activated the result.",
        changed=(*result.changed, *changed),
        next_steps=(*steps, *remaining),
    )


def _current(root: Path, local: Path) -> lock.Locked:
    """Load the Manifest and Lock, naming RESTORE when they disagree."""
    try:
        return lock.load(root, local, outputs=False)
    except GatesealError as exc:
        if exc.code != "lock_mismatch":
            raise
        raise GatesealError(exc.code, str(exc), exc.exit_code, RESTORE) from exc


def _write(
    root: Path, local: Path, plan: Plan, before: dict[str, object]
) -> lock.Changes:
    """Check ownership, store plan's Git release, then write its tracked files."""
    changes = lock.owned_changes(root, plan.files, before)
    if plan.store is not None:
        sources.store(local, *plan.store)
    lock.write_tracked(root, changes)
    return changes


def run(worktree: runtime.Worktree, request: Request) -> Result:
    """Plan every target, write the tracked files, and activate when asked."""
    root, git = worktree.root, worktree.git
    local = runtime.preparation_directory(worktree, create=True)
    artifact = (
        artifact_from_flags(*request.compiler, release=None)
        if request.compiler
        else None
    )
    with contextlib.ExitStack() as stack:
        with runtime.operation_lock(local):
            lock.ensure_policy(root, local, git)
            current = _current(root, local)
            before = decode_json(current.snapshot.lock)
            compiler = None
            if artifact is not None and (artifact.sha256, artifact.version) != (
                current.artifact.sha256,
                current.artifact.version,
            ):
                compiler = stack.enter_context(candidate.installed(artifact, root))
                plan = _plan_candidate(current, request, compiler, git, local)
            else:
                plan = _plan(current, request, artifact or current.artifact, git, local)
            changes = _write(root, local, plan, before)
            if compiler is None:
                # pytestmut: off[stmt.call_remove] -- the files it just wrote load
                lock.load(root, local)
                # pytestmut: on
        result = _result(before, plan, changes.names)
        if request.activate:
            result = _activate(worktree, local, result, compiler)
    return result
