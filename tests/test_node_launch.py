"""The shell script that installs a lockfile once and starts a program: START.

A stand-in for mise prints the folder of the Node that it installed. A
stand-in for npm records its arguments, its npm_config_ variables and the
user configuration it reads, and installs the program of tool-package into
the folder that --prefix names. A stand-in for node renames the install as
FINISH does, unless another install took the name, and runs any other program
as a shell script. test_typescript runs FINISH's checks on the real Node.
"""

import json
import os
import re
import subprocess

import pytest
from gateseal.tools.node import Tree, read_node
from gateseal.validation import canonical
from node_policy import VERSION, node_record

REST = ("/usr/bin", "/bin")
"""The PATH of the hook after the folder of mise."""

NODE_OF_HOOK = read_node(VERSION, node_record())
INSTALL = NODE_OF_HOOK.install
"""The name under which mise keeps the Node of the hook."""

MISE = f"""#!/bin/sh
[ "$*" = "--no-config where {INSTALL}" ] || exit 2
[ ! -e "$FAKE/uninstalled" ] || {{ echo "mise ERROR not installed" >&2; exit 1; }}
echo "$FAKE/node"
"""
"""mise where: it prints the folder of the Node, or fails beside a file
uninstalled."""

LOCK = ".gateseal/tool.package-lock.json"
DEPENDENCIES = {"tool-package": "1.0.0", "other-package": "2.0.0"}
TREE = Tree(
    canonical({"packages": {"": {"dependencies": DEPENDENCIES}}}),
    "node_modules/tool-package/bin/tool.js",
)
WORDS = NODE_OF_HOOK.launch(TREE, LOCK, "tool")[len(NODE_OF_HOOK.prefix) :]
MACHINE = f"{os.uname().sysname}-{os.uname().machine}"
FOLDER = f"{MACHINE}/{re.search(r'/(node-[^;]*); ', WORDS[2])[1]}"

NPM = """#!/bin/sh
printf '%s\\n' "$@" >> "$FAKE/calls"
echo >> "$FAKE/calls"
env | grep -i '^npm_config_' | sort > "$FAKE/settings"
cp "$5" "$FAKE/userconfig"
mkdir -p "$9" && touch "$9/download"
first='#!/bin/sh'
[ ! -e "$FAKE/first" ] || first=$(cat "$FAKE/first")
plant() {
  mkdir -p "$1/node_modules/tool-package/bin"
  printf '%s\\n%s\\n' "$first" "$2" > "$1/node_modules/tool-package/bin/tool.js"
}
plant "$3" 'echo "$PATH"'
[ ! -e "$FAKE/raced" ] || plant "${3%.*}" 'echo winner'
"""
"""npm ci --prefix NEW --userconfig FILE ... --cache NEW/.npm: it copies FILE
to userconfig, writes each npm_config_ variable that it got to settings and
leaves a download in its cache. Beside a file raced another install takes
the final name first. The file first holds the
first line of the program."""

NODE = """#!/bin/sh
[ "$1" = -e ] || exec /bin/sh "$@"
[ -z "$5" ] || [ -e "$5" ] || mv "$3" "$5"
"""
"""node -e FINISH NEW DIGEST DIR renames NEW to DIR unless DIR exists."""


def plant(folder, name, text):
    """Make folder with the program name, a shell script of text."""
    folder.mkdir(parents=True, exist_ok=True)
    script = text if text.startswith("#!") else f"#!/bin/sh\n{text}\n"
    (folder / name).write_text(script)
    (folder / name).chmod(0o755)
    return folder


def start(root, working, first=(), cache=None, more=None, words=WORDS):
    """Start tool with words in working, with the folders first before mise on PATH.

    Return the result and the arguments of each npm call so far. more holds
    more variables of the caller.
    """
    plant(plant(root / "node/bin", "node", NODE), "npm", NPM)
    mise = plant(root / "mise", "mise", MISE)
    path = os.pathsep.join([*map(str, first), str(mise), *REST])
    env = {"PATH": path, "FAKE": str(root), "HOME": str(root / "home")}
    env |= more or {}
    if cache is not None:
        env["XDG_CACHE_HOME"] = cache
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
    """A repository with the lockfile that configure wrote."""
    (root / "repository/.gateseal").mkdir(parents=True)
    (root / "repository" / LOCK).write_bytes(TREE.lockfile)
    return root / "repository"


@pytest.fixture
def final(root):
    """The folder of the install in the home of the test."""
    return root / "home/.cache/gateseal/npm" / FOLDER


def test_the_first_use_installs_the_lockfile_once_and_starts_the_program(
    root, working, final
):
    result, calls = start(root, working)
    node, mise = str(root / "node/bin"), str(root / "mise")
    path = os.pathsep.join([node, str(final / "node_modules/.bin"), node, mise, *REST])
    assert (result.returncode, result.stdout, result.stderr) == (0, f"{path}\n", "")
    new = calls[0][2]
    assert new.startswith(f"{final}.")
    assert calls == [
        [
            "ci",
            "--prefix",
            new,
            "--userconfig",
            f"{new}/.npmrc",
            "--globalconfig",
            "/dev/null",
            "--cache",
            f"{new}/.npm",
            "--ignore-scripts",
            "--no-audit",
            "--no-update-notifier",
            "--logs-max=0",
        ]
    ]
    assert installs(root) == [final.name]
    assert sorted(path.name for path in final.iterdir()) == [
        "node_modules",
        "package-lock.json",
        "package.json",
    ]
    assert (final / "package-lock.json").read_bytes() == TREE.lockfile
    manifest = json.loads((final / "package.json").read_text())
    assert manifest == {"dependencies": DEPENDENCIES}


def test_a_command_that_holds_the_lockfile_needs_no_lockfile_file(root, working, final):
    (working / LOCK).unlink()
    words = NODE_OF_HOOK.launch(TREE, None, "tool")[len(NODE_OF_HOOK.prefix) :]
    result, _ = start(root, working, words=words)
    assert (result.returncode, result.stderr) == (0, "")
    assert (final / "package-lock.json").read_bytes() == TREE.lockfile


@pytest.mark.parametrize(
    "key", ["proxy", "https-proxy", "noproxy", "cafile", "registry"]
)
def test_npm_gets_an_allowed_setting_of_the_user_and_no_other(root, working, key):
    variable = f"npm_config_{key.replace('-', '_')}"
    others = {"npm_config_workspace": "app", "NPM_CONFIG_DRY_RUN": "true"}
    others |= {"npm_config_dry-run": "true"}
    (root / "home").mkdir()
    (root / "home/.npmrc").write_text(
        f"workspace = app\n{key} = y\nglobal=true\n//registry.example/:_authToken=t\n"
        "\tnoproxy[] = z\n"
    )
    result, _ = start(root, working, more={variable: "x"} | others)
    assert (result.returncode, result.stderr) == (0, ""), result.stderr
    assert (root / "settings").read_text() == f"{variable}=x\n"
    assert (root / "userconfig").read_text() == (
        f"{key} = y\n//registry.example/:_authToken=t\n\tnoproxy[] = z\n"
    )


def test_npm_reads_the_user_configuration_that_the_user_names(root, working):
    (root / "other.npmrc").write_text("registry=https://registry.example/\n")
    more = {"NPM_CONFIG_USERCONFIG": str(root / "other.npmrc")}
    result, _ = start(root, working, more=more)
    assert (result.returncode, result.stderr) == (0, "")
    assert (root / "settings").read_text() == ""
    assert (root / "userconfig").read_text() == "registry=https://registry.example/\n"


def test_the_first_line_of_the_program_chooses_no_interpreter(root, working):
    (root / "first").write_text("#!/usr/bin/false")
    result, _ = start(root, working)
    assert (result.returncode, result.stderr) == (0, "")
    assert result.stdout.startswith(f"{root}/node/bin{os.pathsep}")


NO_NODE = (
    "mise has no install of Node 24.22.3, the pinned Node. "
    "Turn the auto-install of mise on.\n"
)

WRONG = "echo 'a program of the PATH ran'; exit 9"


def test_without_the_node_that_mise_installed_nothing_installs(root, working):
    other = plant(plant(root / "other", "node", WRONG), "npm", WRONG)
    (root / "uninstalled").touch()
    result, calls = start(root, working, [other])
    assert (result.returncode, result.stdout, result.stderr) == (127, "", NO_NODE)
    assert calls == []
    assert installs(root) == []


def test_without_the_node_that_mise_installed_no_installed_program_starts(
    root, working
):
    start(root, working)
    (root / "uninstalled").touch()
    result, _ = start(root, working)
    assert (result.returncode, result.stdout, result.stderr) == (127, "", NO_NODE)


def test_an_install_that_stopped_halfway_is_not_used(root, working, final):
    half = final.with_name(f"{final.name}.1")
    plant(half / "node_modules/tool-package/bin", "tool.js", "echo half")
    result, calls = start(root, working)
    assert result.returncode == 0, result.stderr
    assert "half" not in result.stdout
    assert len(calls) == 1


def test_two_first_uses_at_one_time_leave_the_install_that_came_first(root, working):
    (root / "raced").touch()
    result, _ = start(root, working)
    assert (result.returncode, result.stdout) == (0, "winner\n"), result.stderr
    assert installs(root) == [FOLDER.rpartition("/")[2]]


@pytest.mark.parametrize(
    ("cache", "folder"),
    [("{root}/xdg", "xdg"), ("xdg", "home/.cache"), ("", "home/.cache")],
    ids=["absolute", "relative", "empty"],
)
def test_the_install_goes_under_the_cache_folder_of_the_user(
    root, working, cache, folder
):
    result, _ = start(root, working, cache=cache.format(root=root))
    assert result.returncode == 0, result.stderr
    assert (root / folder / "gateseal/npm" / FOLDER / TREE.program).is_file()
    assert not (working / "xdg").exists()
