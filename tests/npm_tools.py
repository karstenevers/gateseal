"""What a test needs to start gateseal's hook of a tool from npm by hand.

pre-commit starts gateseal-npm-tool from its own environment. A test starts
the same module with this test's Python, in the repository and with the
environment of the commit.
"""

import json
import sys
from pathlib import Path

from gateseal.validation import sha
from installation import process, success

HOOK = [sys.executable, "-m", "gateseal.npm_tool"]
"""The words that start the hook's program."""

ASK_NODE = (
    "from gateseal import npm_tool\n"
    "print(npm_tool.pinned_node(*npm_tool.locked_node()))"
)


def hook_entries(root):
    """Return each hook of root's native configuration, by the ID a user names."""
    config = json.loads((root / ".pre-commit-config.yaml").read_bytes())
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    return {hook.get("alias", hook["id"]): hook for hook in hooks}


def hook_command(root, hook):
    """Return the words that start the hook of a tool from npm in root."""
    return [*HOOK, *hook_entries(root)[hook]["args"]]


def pinned_node(root, env):
    """Return the program of the Node that root's Lock pins."""
    node = success([sys.executable, "-c", ASK_NODE], root, env).stdout.strip()
    return f"{node}/node"


def on_node(root, env, *arguments):
    """Return what node prints for arguments on the Node that root's Lock pins."""
    return success([pinned_node(root, env), *arguments], root, env).stdout.strip()


def install_of(root, env, tool):
    """Return the folder in env's cache that holds the packages of tool in root."""
    cache = Path(env["HOME"]) / ".cache"
    lockfile = (root / f".gateseal/{tool}.package-lock.json").read_bytes()
    lock = json.loads((root / "gateseal.lock").read_bytes())
    node = lock["native_artifacts"]["node"]["version"]
    [folder] = [
        path.parent
        for path in cache.glob(f"gateseal/npm/*/node-{node}/*/package-lock.json")
        if path.read_bytes() == lockfile
    ]
    return folder


def record(root, tool, package, lockfile):
    """Make root's Lock record lockfile, npm's lockfile of tool from package."""
    path = f".gateseal/{tool}.package-lock.json"
    (root / path).write_bytes(lockfile)
    lock = json.loads((root / "gateseal.lock").read_bytes())
    lock["components"].setdefault(tool, {})["package"] = package
    lock["outputs"].setdefault(path, {})["sha256"] = sha(lockfile)
    (root / "gateseal.lock").write_text(json.dumps(lock))


def start(root, env, tool, executable, *arguments):
    """Start the hook's program for tool in root, and return the finished process."""
    return process([*HOOK, tool, executable, *arguments], root, env)
