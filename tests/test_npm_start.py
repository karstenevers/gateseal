"""The command that starts npm for gateseal's install hook, run as written.

A stand-in for mise runs the command after exec, as mise exec does with its
auto-install off, and prints the folder of the Node that it installed. Its
npm and an npm first on PATH each record their arguments and PATH.
"""

import os
import subprocess

import pytest
from gateseal.tools.mise import mise_install_name
from gateseal.tools.node import Node, NodeArchive
from gateseal.tools.node_project import start
from gateseal.tools.package_managers import NPM
from stand_ins import executable

NODE = Node(
    "24.22.3",
    {
        "linux-x64": NodeArchive("https://example.invalid/linux.tar.gz", "a" * 64),
        "macos-arm64": NodeArchive("https://example.invalid/macos.tar.gz", "b" * 64),
    },
)
INSTALL = mise_install_name("node", NODE.version, NODE.platforms)

MISE = f"""#!/bin/sh
if [ "$2" = exec ]; then shift 4; exec "$@"; fi
[ "$*" = "--no-config where {INSTALL}" ] || exit 2
[ ! -e "$FAKE/uninstalled" ] || {{ echo "mise ERROR not installed" >&2; exit 1; }}
echo "$FAKE/node"
"""
"""mise exec and mise where. Beside a file uninstalled mise has no install."""

NPM_PROGRAM = """#!/bin/sh
printf '%s\\n' "$0" "$PATH" "$@" > "$FAKE/{name}"
"""
"""An npm that records its path, its PATH and its arguments in a file name."""

NO_NODE = (
    "mise has no install of Node 24.22.3, the pinned Node. "
    "Turn the auto-install of mise on.\n"
)


def run(root, *arguments):
    """Start npm with the hook's command, an npm first on PATH and mise after."""
    node = root / "node/bin"
    executable(node / "node")
    executable(node / "npm", NPM_PROGRAM.format(name="pinned-ran"))
    executable(root / "other/npm", NPM_PROGRAM.format(name="path-ran"))
    executable(root / "mise/mise", MISE)
    path = os.pathsep.join(
        [str(root / "other"), str(root / "mise"), "/usr/bin", "/bin"]
    )
    words = [*start(NODE, NPM), *arguments]
    env = {"PATH": path, "FAKE": str(root)}
    return subprocess.run(words, cwd=root, env=env, capture_output=True, text=True)


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve()


def test_npm_is_the_one_of_the_pinned_install_with_that_folder_first_on_path(root):
    result = run(root, "ci", "--dry-run")
    assert result.returncode == 0, result.stderr
    node = root / "node/bin"
    path = os.pathsep.join([str(node), str(root / "other"), str(root / "mise")])
    assert (root / "pinned-ran").read_text().splitlines() == [
        f"{node}/npm",
        f"{path}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "ci",
        "--dry-run",
    ]
    assert not (root / "path-ran").exists()


def test_without_the_pinned_install_no_npm_runs(root):
    (root / "uninstalled").touch()
    result = run(root, "ci", "--dry-run")
    assert (result.returncode, result.stdout, result.stderr) == (127, "", NO_NODE)
    assert not (root / "pinned-ran").exists()
    assert not (root / "path-ran").exists()
