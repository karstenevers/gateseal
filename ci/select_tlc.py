import json
import os
import re
import subprocess
from typing import cast

runtime = "runtime.toml"
lock = "src/gateseal/data/runtime.lock"


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def pre_commit_pins(revision: str) -> tuple[str, str]:
    import tomllib

    authored = tomllib.loads(git("show", f"{revision}:{runtime}").decode())
    shipped = json.loads(git("show", f"{revision}:{lock}"))
    requirements = authored["pre-commit"]["requirements"]
    if not isinstance(requirements, list) or not all(
        isinstance(r, str) for r in cast(list[object], requirements)
    ):
        raise ValueError("invalid pre-commit requirements")
    requested = [
        match.group(1)
        for requirement in cast(list[str], requirements)
        if (match := re.fullmatch(r"pre[-_.]commit==([^\s;]+)", requirement, re.I))
    ]
    packages = shipped["pre_commit"]["packages"]
    pinned = [p["version"] for p in packages if p["name"] == "pre-commit"]
    if (
        len(requested) != 1
        or len(pinned) != 1
        or not isinstance(pinned[0], str)
        or not pinned[0]
    ):
        raise ValueError("expected one pre-commit pin in each runtime input")
    if shipped["pre_commit"]["version"] != pinned[0]:
        raise ValueError("shipped pre-commit version disagrees with its package")
    return requested[0], pinned[0]


required = True
if os.environ["EVENT_NAME"] in ("push", "pull_request"):
    try:
        head = os.environ["HEAD_SHA"]
        if os.environ["EVENT_NAME"] == "pull_request":
            base = git("merge-base", os.environ["BASE_SHA"], head).decode().strip()
        else:
            base = os.environ["BEFORE_SHA"]
            if not base or set(base) == {"0"}:
                raise ValueError("push has no comparison base")
        paths = git(
            "diff", "--no-renames", "--name-only", "-z", base, head, "--"
        ).split(b"\0")
        required = any(p.startswith(b"formal/") for p in paths)
        if not required and ({runtime.encode(), lock.encode()} & set(paths)):
            required = pre_commit_pins(base) != pre_commit_pins(head)
    except (subprocess.CalledProcessError, ValueError, KeyError, TypeError) as error:
        print(f"TLC selected because comparison is unavailable: {error}")
        required = True
with open(os.environ["GITHUB_OUTPUT"], "a") as output:
    output.write(f"required={str(required).lower()}\n")
