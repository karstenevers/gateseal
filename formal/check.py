"""Model-check gateseal's prepared-attempt lifecycle with TLC.

The code row describes src/gateseal and must hold every invariant.
Each variant changes one design decision and must break the invariants that
decision guards, which shows that every invariant can fail.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# TLC's v1.8.0 is a rolling pre-release whose jar is re-uploaded; 1.7.4 is
# immutable.
TLC_TOOL = (
    "github:tlaplus/tlaplus[asset_pattern=tla2tools.jar,"
    "checksum=sha256:936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88]"
    "@1.7.4"
)
JAVA_TOOL = "java@temurin-25"
SET_TLC = "Set TLA2TOOLS to tla2tools.jar 1.7.4 and JAVA to Java 11 or later."
INVARIANTS = (
    "CurrentIsComplete",
    "PinnedAttemptLives",
    "NoLostEdits",
    "TruthfulReports",
    "HookBeforePublish",
    "StartupFailureNeedsTwoPublishes",
    "InterpreterLives",
)
CODE = {
    "CommitLock": True,
    "KeepReplaced": True,
    "DropRestored": True,
    "WaitForOrphans": True,
    "HookFirst": True,
    "RecordLast": True,
    "StoreLock": True,
    "RefBeforeUse": True,
    "LiveWhileDir": True,
    "SealChecked": True,
}
VARIANTS = {
    "no commit lock": ({"CommitLock": False}, ("PinnedAttemptLives", "NoLostEdits")),
    "cleanup keeps only current": (
        {"KeepReplaced": False},
        ("StartupFailureNeedsTwoPublishes",),
    ),
    "restored patches reported": ({"DropRestored": False}, ("TruthfulReports",)),
    "patch dropped while its checkout runs": (
        {"WaitForOrphans": False},
        ("NoLostEdits",),
    ),
    "rename before hook": ({"HookFirst": False}, ("HookBeforePublish",)),
    "rename before record": ({"RecordLast": False}, ("CurrentIsComplete",)),
    "no store lock": ({"StoreLock": False}, ("InterpreterLives",)),
    "link after the build": ({"RefBeforeUse": False}, ("InterpreterLives",)),
    "links live only with a record": ({"LiveWhileDir": False}, ("InterpreterLives",)),
    "entry used without its seal": ({"SealChecked": False}, ("InterpreterLives",)),
}
BOUNDS = """\
    Commits = {c1, c2}
    AttemptOrder <- MCAttemptOrder
    MaxActivations = 3
    None = None
"""


def configuration(flags: dict[str, bool], invariants: tuple[str, ...]) -> str:
    constants = "".join(
        f"    {name} = {str(value).upper()}\n" for name, value in flags.items()
    )
    checks = "".join(f"INVARIANT {name}\n" for name in ("TypeOK", *invariants))
    return (
        f"SPECIFICATION Spec\nCONSTANTS\n{BOUNDS}{constants}"
        f"SYMMETRY MCSymmetry\n{checks}"
    )


def mise(arguments: list[str], ceiling: str) -> subprocess.CompletedProcess[str]:
    env = os.environ | {"MISE_CEILING_PATHS": ceiling}
    try:
        return subprocess.run(
            ["mise", *arguments],
            cwd=ceiling,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        sys.exit(f"Install mise (https://mise.jdx.dev), or: {SET_TLC}")


def tlc_command() -> list[str]:
    """Return the java command for TLC, installing it with mise when needed."""
    jar, java = os.environ.get("TLA2TOOLS"), os.environ.get("JAVA")
    if not jar:
        with tempfile.TemporaryDirectory(prefix="gateseal-mise-") as ceiling:
            tools = [TLC_TOOL] if java else [JAVA_TOOL, TLC_TOOL]
            installed = mise(["install", "--quiet", *tools], ceiling)
            if installed.returncode:
                sys.exit(f"mise could not install TLC:\n{installed.stderr}{SET_TLC}")
            jar = str(
                Path(mise(["where", TLC_TOOL], ceiling).stdout.strip())
                / "tla2tools.jar"
            )
            if not java:
                java = mise(
                    ["which", "java", "--tool", JAVA_TOOL], ceiling
                ).stdout.strip()
    for name, path in (("TLA2TOOLS", jar), ("JAVA", java)):
        if not path or not Path(path).is_file():
            sys.exit(f"{name} is {path!r}, which is not a file. {SET_TLC}")
    return [str(java), "-XX:+UseParallelGC", "-cp", jar, "tlc2.TLC"]


def run(
    tlc: list[str], flags: dict[str, bool], invariants: tuple[str, ...]
) -> tuple[str | None, str]:
    """Return the violated invariant, or None when every check holds."""
    with tempfile.TemporaryDirectory(prefix="gateseal-tlc-") as scratch:
        work = Path(scratch)
        for name in ("PreparedAttempts.tla", "InterpreterStore.tla", "MC.tla"):
            shutil.copy(HERE / name, work / name)
        (work / "MC.cfg").write_text(configuration(flags, invariants))
        result = subprocess.run(
            [*tlc, "-workers", "auto", "-metadir", str(work / "states"), "MC.tla"],
            cwd=work,
            capture_output=True,
            text=True,
            check=False,
        )
    output = result.stdout + result.stderr
    violated = re.search(r"Error: Invariant (\w+) is violated", output)
    if violated:
        return violated.group(1), output
    if "Model checking completed. No error has been found." in output:
        return None, output
    sys.exit(f"TLC did not finish a check:\n{output}")


def states(output: str) -> str:
    found = re.findall(r"([\d,]+) distinct states found", output)
    return found[-1] if found else "?"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trace",
        metavar="ROW",
        help="print TLC's full output for 'code' or one variant name",
    )
    arguments = parser.parse_args()
    tlc = tlc_command()
    if arguments.trace:
        if arguments.trace == "code":
            flags, invariants = CODE, INVARIANTS
        else:
            change, invariants = VARIANTS[arguments.trace]
            flags = CODE | change
        print(run(tlc, flags, invariants)[1])
        return 0
    failures = 0
    rows = [("code", CODE, INVARIANTS, None)] + [
        (name, CODE | change, (invariant,), invariant)
        for name, (change, broken) in VARIANTS.items()
        for invariant in broken
    ]
    for name, flags, invariants, expected in rows:
        started = time.monotonic()
        violated, output = run(tlc, flags, invariants)
        seconds = time.monotonic() - started
        ok = violated == expected
        failures += not ok
        outcome = f"violates {violated}" if violated else "holds"
        print(
            f"{'ok  ' if ok else 'FAIL'} {name}: {outcome}, "
            f"{states(output)} states, {seconds:.0f} s"
        )
        if not ok:
            print(output)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
