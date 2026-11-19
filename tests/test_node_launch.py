"""The shell script that installs packages once and starts a program: START.

A stand-in for mise prints the folder of the Node that it installed. A
stand-in for npm records its arguments and installs the package tool-package
into the folder that --prefix names, with its program tool linked into
node_modules/.bin as npm links it. A stand-in for node renames a folder as
fs.renameSync does: it fails when the new name is taken.
"""

import os
import subprocess

import pytest
from gateseal.tools.node import NPM_INSTALL, Package, install_folder, launch

REST = ("/usr/bin", "/bin")
"""The PATH of the hook after the folder of mise."""

INSTALL = "http:node-0123456789ab@24.22.3"
"""The name under which mise keeps the Node of the hook."""

MISE = f"""#!/bin/sh
[ "$*" = "--no-config where {INSTALL}" ] || exit 2
[ ! -e "$FAKE/uninstalled" ] || {{ echo "mise ERROR not installed" >&2; exit 1; }}
[ ! -e "$FAKE/hollow" ] || {{ echo "$FAKE/hollow"; exit; }}
echo "$FAKE/node"
"""
"""mise where: it prints the folder of the Node. Beside a file uninstalled it
has no install, and beside hollow its folder holds no node."""

PACKAGES = [Package("tool-package", "1.0.0"), Package("other-package", "2.0.0")]
MACHINE = f"{os.uname().sysname}-{os.uname().machine}"
FOLDER = f"{MACHINE}/{install_folder('24.22.3', PACKAGES)}"

NPM = """#!/bin/sh
printf '%s\\n' "$@" >> "$FAKE/calls"
echo >> "$FAKE/calls"
[ ! -e "$FAKE/away" ] || { echo "registry away" >&2; exit 7; }
while [ "$1" != --prefix ]; do shift; done
echo "added 2 packages"
mkdir -p "$4" && touch "$4/download"
plant() {
  mkdir -p "$1/node_modules/.bin" "$1/node_modules/$2"
  printf '#!/bin/sh\\n%s\\n' "$3" > "$1/node_modules/$2/tool"
  chmod +x "$1/node_modules/$2/tool"
  ln -s "../$2/tool" "$1/node_modules/.bin/tool"
}
owner=tool-package
[ ! -e "$FAKE/shadowed" ] || owner=other-package
[ -e "$FAKE/bare" ] || plant "$2" "$owner" 'echo "$PATH"'
[ ! -e "$FAKE/raced" ] || plant "${2%.*}" tool-package 'echo winner'
"""
"""npm: it leaves a download in the folder that --cache names. Beside a file
away it fails, beside bare it installs no program and still succeeds, beside
shadowed the other package links the program, and beside raced another
install takes the final name first."""

NODE = """#!/bin/sh
[ "$1" = -e ] || exit 0
[ ! -e "$FAKE/locked" ] && [ ! -e "$4" ] && mv "$3" "$4"
"""
"""node -e SCRIPT OLD NEW renames OLD to NEW, or fails beside a file locked."""


def plant(folder, name, text):
    """Make folder with the program name, a shell script of text."""
    folder.mkdir(parents=True, exist_ok=True)
    script = text if text.startswith("#!") else f"#!/bin/sh\n{text}\n"
    (folder / name).write_text(script)
    (folder / name).chmod(0o755)
    return folder


def start(root, working, first=(), cache=None, npm=NPM):
    """Start tool in working, with the folders first before mise on PATH.

    Return the result and the arguments of each npm call so far. Without
    npm, the Node that mise installed has no npm.
    """
    node = plant(root / "node/bin", "node", NODE)
    if npm:
        plant(node, "npm", npm)
    mise = plant(root / "mise", "mise", MISE)
    path = os.pathsep.join([*map(str, first), str(mise), *REST])
    env = {"PATH": path, "FAKE": str(root), "HOME": str(root / "home")}
    if cache is not None:
        env["XDG_CACHE_HOME"] = cache
    words = launch("24.22.3", INSTALL, PACKAGES, "tool")
    result = subprocess.run(words, cwd=working, env=env, capture_output=True, text=True)
    calls = root / "calls"
    text = calls.read_text() if calls.exists() else ""
    return result, [call.split("\n") for call in text.split("\n\n")[:-1]]


def installs(root):
    """Return the names of the folders beside the install, final or not."""
    parent = (root / "home/.cache/gateseal/npm" / FOLDER).parent
    return sorted(path.name for path in parent.iterdir()) if parent.exists() else []


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve()


@pytest.fixture
def working(root):
    """A repository that has its own program named tool."""
    return plant(root / "repository/node_modules/.bin", "tool", "echo own").parents[1]


@pytest.fixture
def final(root):
    """The folder of the install in the home of the test."""
    return root / "home/.cache/gateseal/npm" / FOLDER


def test_the_first_use_installs_once_and_starts_the_program(root, working, final):
    result, calls = start(root, working)
    node = str(root / "node/bin")
    programs = str(final / "node_modules/.bin")
    path = os.pathsep.join([node, programs, node, str(root / "mise"), *REST])
    assert (result.returncode, result.stdout) == (0, f"{path}\n"), result.stderr
    assert result.stderr == ""
    assert len(calls) == 1
    assert calls[0][:-8] == list(NPM_INSTALL[1:])
    new = calls[0][-7]
    assert new.startswith(f"{final}.")
    assert calls[0][-8:-4] == ["--prefix", new, "--cache", f"{new}/.npm"]
    assert calls[0][-4:-2] == ["--logs-dir", f"{new}/.npm/_logs"]
    assert calls[0][-2:] == ["tool-package@1.0.0", "other-package@2.0.0"]
    assert installs(root) == [final.name]
    assert sorted(path.name for path in final.iterdir()) == ["node_modules"]


def test_a_later_use_calls_no_npm(root, working):
    start(root, working)
    (root / "away").touch()
    result, calls = start(root, working)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 1


NO_NODE = (
    "mise has no install of Node 24.22.3, the pinned Node. "
    "Turn the auto-install of mise on.\n"
)

WRONG = "echo 'a program of the PATH ran'; exit 9"


@pytest.mark.parametrize("state", ["uninstalled", "hollow"])
def test_without_the_node_that_mise_installed_nothing_installs(root, working, state):
    other = plant(plant(root / "other", "node", WRONG), "npm", WRONG)
    (root / state).mkdir()
    result, calls = start(root, working, [other])
    assert (result.returncode, result.stdout, result.stderr) == (1, "", NO_NODE)
    assert calls == []
    assert installs(root) == []


def test_without_the_node_that_mise_installed_no_installed_program_starts(
    root, working
):
    start(root, working)
    (root / "uninstalled").touch()
    result, _ = start(root, working)
    assert (result.returncode, result.stdout, result.stderr) == (1, "", NO_NODE)


def test_no_npm_but_the_one_of_the_node_installs(root, working):
    other = plant(root / "other", "npm", WRONG)
    result, calls = start(root, working, [other], npm=None)
    assert (result.returncode, result.stdout) == (127, "")
    assert "npm" in result.stderr
    assert calls == []
    assert installs(root) == []


def test_a_node_and_an_npm_first_on_path_do_not_run(root, working):
    other = plant(plant(root / "other", "node", WRONG), "npm", WRONG)
    result, calls = start(root, working, [other])
    assert result.returncode == 0, result.stderr
    assert result.stdout.split(os.pathsep)[0] == str(root / "node/bin")
    assert len(calls) == 1


def test_a_failed_install_stops_the_hook_with_the_status_of_npm(root, working):
    (root / "away").touch()
    result, calls = start(root, working)
    assert (result.returncode, result.stdout) == (7, "")
    assert result.stderr == "registry away\n"
    assert len(calls) == 1
    assert installs(root) == []


def test_an_install_that_stopped_halfway_is_not_used(root, working, final):
    half = final.with_name(f"{final.name}.1") / "node_modules"
    plant(half / "tool-package", "tool", "echo half")
    (half / ".bin").mkdir()
    (half / ".bin/tool").symlink_to("../tool-package/tool")
    result, calls = start(root, working)
    assert result.returncode == 0, result.stderr
    assert "half" not in result.stdout
    assert len(calls) == 1


def missing(final):
    return (
        f"No program tool of tool-package in {final}. "
        "Delete the folder to install again.\n"
    )


@pytest.mark.parametrize("first", [(), ("other/bin",)])
@pytest.mark.parametrize("flaw", ["bare", "shadowed"])
def test_an_install_without_the_program_of_the_first_package_is_not_kept(
    root, working, first, flaw
):
    (root / flaw).touch()
    folders = [plant(root / name, "tool", "echo other") for name in first]
    result, _ = start(root, working, folders)
    assert (result.returncode, result.stdout) == (127, "")
    assert result.stderr == "npm installed no program tool of tool-package.\n"
    assert installs(root) == []
    (root / flaw).unlink()
    result, calls = start(root, working, folders)
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2


@pytest.mark.parametrize("damage", ["unlinked", "a file", "not executable"])
def test_a_folder_whose_program_changed_stops_the_hook(root, working, final, damage):
    start(root, working)
    link = final / "node_modules/.bin/tool"
    if damage == "not executable":
        link.resolve().chmod(0o644)
    else:
        link.unlink()
    if damage == "a file":
        plant(link.parent, "tool", "echo planted")
    result, calls = start(root, working)
    assert (result.returncode, result.stdout) == (127, "")
    assert result.stderr == missing(final)
    assert len(calls) == 1


def test_two_first_uses_at_one_time_leave_the_install_that_came_first(root, working):
    (root / "raced").touch()
    result, _ = start(root, working)
    assert (result.returncode, result.stdout) == (0, "winner\n"), result.stderr
    assert installs(root) == [FOLDER.rpartition("/")[2]]


def test_an_install_that_cannot_get_its_name_stops_the_hook(root, working, final):
    (root / "locked").touch()
    result, _ = start(root, working)
    assert (result.returncode, result.stdout) == (1, "")
    assert result.stderr == f"cannot write {final}\n"
    assert installs(root) == []


@pytest.mark.parametrize(
    ("cache", "folder"),
    [("{root}/xdg", "xdg"), ("xdg", "home/.cache"), ("", "home/.cache")],
    ids=["absolute", "relative", "empty"],
)
def test_the_install_goes_under_the_cache_folder_of_the_user(
    root, working, cache, folder
):
    result, _ = start(root, working, cache=cache.format(root=root))
    final = root / folder / "gateseal/npm" / FOLDER
    assert result.returncode == 0, result.stderr
    assert str(final / "node_modules/.bin") in result.stdout.split(os.pathsep)
    assert not (working / "xdg").exists()
