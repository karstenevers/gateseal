"""The Hook Python: the CPython a policy pins for its Python-language hooks.

Without a pin, every hook runs on the Compiler Runtime's CPython. With one,
activation installs the pinned CPython into the Interpreter Store beside the
runtime's and links it into the attempt's tools directory as python<version>.
The compiler renders that name as the language_version of each hook whose
kind runs on Python, and pre-commit passes it to virtualenv, which finds the
link first on the hooks' PATH. No host Python is ever used.

policy.toml reviews and pins it as [components.python], and its Policy Lock
record of each reviewed version is uv's download metadata of that CPython for
every supported target, which gateseal policy lock reads from the
python-build-standalone build's SHA256SUMS.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass

from gateseal.bootstrap import hook_python_name
from gateseal.compiler_release import (
    RELEASES,
    Target,
    cpython_release,
    for_target,
    interpreter_records,
    interpreters,
    load_runtime,
)
from gateseal.diagnostics import GatesealError, Note
from gateseal.tools.model import Catalog, MissingUpstream, Upstream, changed_upstream

COMPONENT = "python"
"""Its name in policy.toml's components, gateseal policy add and pin, and the Lock."""

LATEST = (
    "https://raw.githubusercontent.com/astral-sh/python-build-standalone/"
    "latest-release/latest-release.json"
)
"""The file that names python-build-standalone's newest build, as uv reads it."""


@dataclass(frozen=True)
class HookPython:
    """The pinned CPython release, with uv's download metadata for each target."""

    version: str
    interpreters: Mapping[str, dict[str, object]]

    @property
    def build(self) -> str:
        return str(next(iter(self.interpreters.values()))["build"])

    @property
    def name(self) -> str:
        """Return the hooks' language_version, which is also its link's name.

        pre-commit passes it to virtualenv unchanged on macOS and Linux, and
        virtualenv reads python<version> as a version request that it looks up
        by name on PATH. The configuration is tracked, so it cannot name the
        store's path, and virtualenv reads a bare name without the version as
        an implementation rather than a file to find.
        """
        return hook_python_name(self.version)

    @property
    def on_runtime(self) -> bool:
        """Return whether it is this compiler release's own CPython release.

        pre-commit runs a hook whose language_version names its own CPython
        release on that interpreter, so hooks then run on the Compiler Runtime.
        """
        return self.version == cpython_release(load_runtime().python)

    def interpreter(self, target: Target) -> dict[str, dict[str, object]]:
        """Return uv's download metadata with target's one record."""
        return for_target(self.interpreters, target)

    def lock_entry(self) -> dict[str, object]:
        """Return the Lock's components record of the Hook Python."""
        return {"version": self.version, "python": dict(self.interpreters)}

    def note(self) -> Note:
        if self.on_runtime:
            return Note(
                "info",
                "hook_python",
                f"The policy's Hook Python, CPython {self.version}, is this "
                "gateseal release's own CPython, so Python-language hooks run on "
                "the Compiler Runtime.",
            )
        return Note(
            "info",
            "hook_python",
            f"Python-language hooks run on the policy's Hook Python, CPython "
            f"{self.version} build {self.build}.",
        )


def offer(catalog: Catalog, records: Mapping[str, object]) -> HookPython:
    """Return the pinned Hook Python, with each reviewed version's record checked.

    A record that is not one CPython release's metadata for every target
    fails with invalid_field.
    """
    pinned: dict[str, dict[str, object]] = {}
    for version in catalog.requires:
        field = f"components.{COMPONENT}.versions.{version}"
        python = interpreters(records[version], field)
        if cpython_release(python) != version:
            raise GatesealError(
                "invalid_field", f"{field} records CPython {cpython_release(python)}."
            )
        if version == catalog.pin:
            pinned = python
    return HookPython(catalog.pin, pinned)


def lock(version: str, recorded: object, upstream: Upstream) -> dict[str, object]:
    """Return a reviewed version's Policy Lock record, read from SHA256SUMS.

    A recorded version is checked against its own build's SHA256SUMS, which
    never changes. A new one comes from python-build-standalone's newest build,
    which carries only the newest patch release of each CPython minor.
    """
    build = _recorded_build(recorded) or _latest(upstream)
    sums = upstream.download(f"{RELEASES}{build}/SHA256SUMS")
    if sums is None:
        raise MissingUpstream(
            "upstream_checksums_missing",
            f"python-build-standalone build {build} has no SHA256SUMS.",
            COMPONENT,
            version,
        )
    try:
        record = interpreter_records(sums.decode("utf-8", "replace"), version, build)
    except GatesealError as exc:
        raise MissingUpstream(
            exc.code,
            f"{exc} Each build carries only the newest patch release of a "
            "CPython minor.",
            COMPONENT,
            version,
        ) from exc
    if recorded is not None and recorded != record:
        raise changed_upstream(
            COMPONENT, version, f"SHA256SUMS of build {build}", recorded, record
        )
    return dict(record)


def _recorded_build(recorded: object) -> str | None:
    """Return the build of a recorded version, or None to read a new one."""
    if recorded is None:
        return None
    try:
        python = interpreters(recorded, f"components.{COMPONENT}")
    except GatesealError:
        return None
    return str(next(iter(python.values()))["build"])


def _latest(upstream: Upstream) -> str:
    data = upstream.download(LATEST)
    try:
        build = json.loads(data or b"")["tag"]
    except ValueError, TypeError, KeyError:
        build = None
    if not isinstance(build, str) or not build.isdigit() or len(build) != 8:
        raise GatesealError(
            "upstream_unavailable",
            f"{LATEST} names no python-build-standalone build.",
            4,
        )
    return build
