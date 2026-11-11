"""Generate runtime.lock, the Python and pre-commit each gateseal release carries.

Edit runtime.toml, then run from the repository root:

    uv run python scripts/lock_runtime.py

The CPython digests come from the python-build-standalone release's SHA256SUMS.
uv resolves the pre-commit pins once for each target, and every package,
version and wheel must be one the pins account for. On a refusal the script
names the reason and writes nothing.
"""

import argparse
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from gateseal.compiler_release import (
    RELEASES,
    TARGETS,
    interpreter_records,
    parse_runtime,
)
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import write_atomic
from gateseal.tools import built_in_tools
from gateseal.tools.model import require_local_hooks
from gateseal.validation import canonical, version

PACKAGE = Path(__file__).resolve().parents[1]
INPUT = PACKAGE / "runtime.toml"
OUTPUT = PACKAGE / "src" / "gateseal" / "data" / "runtime.lock"
COMMAND = "uv run python scripts/lock_runtime.py"
INDEX = "https://pypi.org/simple"
PIN = re.compile(r"([a-z0-9]+(?:-[a-z0-9]+)*)==([0-9A-Za-z.!+-]+)")


class Refusal(Exception):
    """A reason to write nothing."""


@dataclass(frozen=True)
class Request:
    """runtime.toml: the uv floor, the CPython release, and pre-commit's pins."""

    minimum_uv: str
    python: str
    build: str
    pins: Mapping[str, str]


def _table(value: object, names: set[str], field: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != names:
        raise Refusal(f"{field} requires exactly {', '.join(sorted(names))}.")
    return value


def pins(requirements: object) -> dict[str, str]:
    """Return each pinned package's version by its normalized name."""
    if not isinstance(requirements, list):
        raise Refusal("pre-commit.requirements must be an array.")
    found: dict[str, str] = {}
    for requirement in requirements:
        match = PIN.fullmatch(requirement) if isinstance(requirement, str) else None
        if match is None:
            raise Refusal(
                f"{requirement!r} is not a name==version pin with a normalized name."
            )
        if match[1] in found:
            raise Refusal(f"runtime.toml pins {match[1]} twice.")
        found[match[1]] = match[2]
    if "pre-commit" not in found:
        raise Refusal("runtime.toml pins no pre-commit.")
    return found


def _toml(data: bytes, what: str) -> dict[str, object]:
    import tomllib

    try:
        return tomllib.loads(data.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise Refusal(f"{what} is not valid UTF-8 TOML: {exc}") from exc


def parse_request(data: bytes) -> Request:
    document = _table(
        _toml(data, "runtime.toml"),
        {"minimum_uv", "python", "pre-commit"},
        "runtime.toml",
    )
    python = _table(document["python"], {"version", "build"}, "python")
    build = python["build"]
    if not isinstance(build, str) or not re.fullmatch("[0-9]{8}", build):
        raise Refusal("python.build must be an eight-digit release date.")
    requirements = _table(document["pre-commit"], {"requirements"}, "pre-commit")
    return Request(
        version(document["minimum_uv"], "minimum_uv"),
        version(python["version"], "python.version"),
        build,
        pins(requirements["requirements"]),
    )


def _resolved(
    pylock: bytes, platform: str
) -> list[tuple[str, str, list[dict[str, str]]]]:
    """Return each package of a pylock.toml with its version and wheel records."""
    document = _toml(pylock, f"uv's resolution for {platform}")
    try:
        return [
            (
                package["name"],
                package["version"],
                [
                    {
                        "filename": unquote(urlsplit(wheel["url"]).path.split("/")[-1]),
                        "sha256": wheel["hashes"]["sha256"],
                        "url": wheel["url"],
                    }
                    for wheel in package.get("wheels", [])
                ],
            )
            for package in document["packages"]
        ]
    except (KeyError, TypeError, AttributeError) as exc:
        raise Refusal(f"uv wrote an unreadable resolution for {platform}.") from exc


def packages(
    resolutions: Mapping[str, bytes], pinned: Mapping[str, str]
) -> list[dict[str, object]]:
    """Merge each target's resolution into one closure of the pinned packages.

    resolutions holds uv's pylock.toml by target platform. Every package must
    be pinned, at its pin, with a wheel, and one wheel file has one digest.
    """
    wheels: dict[str, dict[str, dict[str, str]]] = {name: {} for name in pinned}
    for platform, pylock in resolutions.items():
        resolved: set[str] = set()
        for name, found, artifacts in _resolved(pylock, platform):
            if name not in pinned:
                raise Refusal(
                    f"The resolution for {platform} adds {name} {found}, which "
                    "runtime.toml does not pin."
                )
            if found != pinned[name]:
                raise Refusal(
                    f"The resolution for {platform} selects {name} {found}, but "
                    f"runtime.toml pins {pinned[name]}."
                )
            if not artifacts:
                raise Refusal(f"{name} {found} has no wheel for {platform}.")
            for record in artifacts:
                known = wheels[name].setdefault(record["filename"], record)
                if known["sha256"] != record["sha256"]:
                    raise Refusal(f"{record['filename']} has two SHA-256 digests.")
            resolved.add(name)
        missing = sorted(set(pinned) - resolved)
        if missing:
            raise Refusal(
                f"The resolution for {platform} lacks {', '.join(missing)}, which "
                "runtime.toml pins."
            )
    return [
        {
            "name": name,
            "version": pinned[name],
            "artifacts": [files[filename] for filename in sorted(files)],
        }
        for name, files in sorted(wheels.items())
    ]


def render(
    request: Request, python: Mapping[str, object], closure: list[dict[str, object]]
) -> bytes:
    return canonical(
        {
            "generated_by": COMMAND,
            "minimum_uv": request.minimum_uv,
            "python": python,
            "pre_commit": {"version": request.pins["pre-commit"], "packages": closure},
        }
    )


def download(url: str) -> str:
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            return response.read().decode("utf-8")
    except (OSError, UnicodeError, urllib.error.URLError) as exc:
        raise Refusal(f"Cannot download {url}: {exc}") from exc


def resolve(request: Request, platform: str) -> bytes:
    """Return uv's pylock.toml for the pins on one target.

    uv reads no configuration file and no UV_ or PIP_ variable, so only the
    pins, the target and PyPI decide the result. Other variables stay, so TLS
    trust and proxies still work.
    """
    requirements = "".join(f"{name}=={pin}\n" for name, pin in request.pins.items())
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "PIP_"))
    }
    result = subprocess.run(
        ["uv", "pip", "compile", "-", "--no-config", "--format", "pylock.toml"]
        + ["--no-header", "--no-annotate", "-q", "--python-platform", platform]
        + ["--python-version", request.python, "--default-index", INDEX],
        input=requirements.encode(),
        capture_output=True,
        env=environment,
        check=False,
    )
    if result.returncode:
        reason = result.stderr.decode(errors="replace").strip()
        raise Refusal(f"uv cannot resolve the pins for {platform}: {reason}")
    return result.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    output = parser.parse_args(argv).output
    try:
        request = parse_request(INPUT.read_bytes())
        sums = download(f"{RELEASES}{request.build}/SHA256SUMS")
        python = interpreter_records(sums, request.python, request.build)
        resolutions = {
            target.platform: resolve(request, target.platform)
            for target in TARGETS.values()
        }
        data = render(request, python, packages(resolutions, request.pins))
        require_local_hooks(
            (
                hook
                for tool in built_in_tools().values()
                if tool.source.local
                for hook in tool.hooks.values()
            ),
            parse_runtime(data).pre_commit,
        )
    except (Refusal, GatesealError) as exc:
        print(f"lock_runtime: {exc}", file=sys.stderr)
        return 1
    write_atomic(output.resolve(), data, 0o644)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
