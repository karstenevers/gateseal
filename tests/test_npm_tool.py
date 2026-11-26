"""gateseal's hook that starts a tool from npm: gateseal-npm-tool.

Each test runs the hook's program in a repository whose Lock records a Node, a
tool and its lockfile. A stand-in for mise prints the folder of the Node that
it installed. A stand-in for npm records its arguments, its npm_config_
variables and the user configuration it reads, and installs the program of
tool-package into the folder that --prefix names. A stand-in for node passes
the install's check, and runs any other program as a shell script.
test_typescript runs the check on the real Node.
"""

import json
import os
import platform
import subprocess
import sys
import tempfile

import pytest
from gateseal import npm_tool
from gateseal.tools.mise import mise_request
from gateseal.tools.node import NAME, read_node
from gateseal.validation import canonical, sha
from node_policy import VERSION, node_record
from stand_ins import executable

REST = "/usr/bin:/bin"
"""The PATH of the hook after the folder of mise."""

NODE = read_node(VERSION, node_record())
REQUEST = mise_request(NAME, VERSION, NODE.platforms)
INSTALL = f"{REQUEST.partition('[')[0]}@{VERSION}"
"""The name under which mise keeps the Node of the Lock."""

MISE = f"""#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE/asked"
if [ "$2" = exec ]; then
  [ ! -e "$FAKE/unreachable" ] || {{
    echo "mise ERROR download failed" >&2
    echo "mise ERROR Run with --verbose or MISE_VERBOSE=1 for more information" >&2
    exit 1
  }}
  [ ! -e "$FAKE/silent" ] || exit 3
  [ ! -e "$FAKE/installs" ] || rm "$FAKE/uninstalled"
  shift 4; exec "$@"
fi
[ "$*" = "--no-config where {INSTALL}" ] || exit 2
[ ! -e "$FAKE/uninstalled" ] || {{ echo "mise ERROR not installed" >&2; exit 1; }}
echo "$FAKE/node"
"""
"""mise where prints the folder of the Node, or fails beside a file
uninstalled. mise exec starts the command, and beside a file installs it
installs the Node first."""

PATH = ".gateseal/tool.package-lock.json"
DEPENDENCIES = {"tool-package": "1.0.0", "other-package": "2.0.0"}
LOCKFILE = canonical(
    {
        "packages": {
            "": {"dependencies": DEPENDENCIES},
            "node_modules/tool-package": {"bin": {"tool": "bin/tool.js"}},
        }
    }
)
LOCK = {
    "native_artifacts": {"node": NODE.lock_entry()[2]},
    "components": {"tool": {"package": "tool-package"}},
    "outputs": {PATH: {"sha256": sha(LOCKFILE)}},
}
MACHINE = f"{platform.system()}-{platform.machine()}"

NPM = """#!/bin/sh
printf '%s\\n' "$@" >> "$FAKE/calls"
echo >> "$FAKE/calls"
env | grep -i '^npm_config_' | sort > "$FAKE/settings"
cp "$5" "$FAKE/userconfig"
cp "$7" "$FAKE/globalconfig"
mkdir -p "$9" && touch "$9/download"
first='#!/bin/sh'
[ ! -e "$FAKE/first" ] || first=$(cat "$FAKE/first")
plant() {
  mkdir -p "$1/node_modules/tool-package/bin"
  printf '%s\\n%s\\n' "$first" "$2" > "$1/node_modules/tool-package/bin/tool.js"
}
plant "$3" 'echo "$PATH"; echo "$@"; exit "${STATUS:-0}"'
[ ! -e "$FAKE/raced" ] || plant "${3%.*}" 'echo winner'
[ ! -e "$FAKE/offline" ] || { echo "npm error" >&2; exit 1; }
"""
"""npm ci --prefix NEW --userconfig FILE --globalconfig FILE --cache NEW/.npm:
it copies each FILE, writes each npm_config_ variable that it got to settings
and leaves a download in its cache. The program that it installs prints its
PATH and its arguments. Beside a file raced another install takes the final
name first, and beside a file offline npm fails. The file first holds the
first line of the program."""

NODE_PROGRAM = """#!/bin/sh
[ "$1" = -e ] || exec /bin/sh "$@"
printf '%s\\n' "$3" > "$FAKE/checked"
[ ! -e "$FAKE/incomplete" ] || { echo "npm installed no package." >&2; exit 1; }
"""
"""node -e CHECK NEW passes unless beside a file incomplete, and node runs any
other program as a shell script."""


@pytest.fixture
def root(tmp_path, monkeypatch):
    """The folder of the stand-ins, with the hook's environment around it."""
    root = tmp_path.resolve()
    executable(root / "node/bin/node", NODE_PROGRAM)
    executable(root / "node/bin/npm", NPM)
    executable(root / "mise/mise", MISE)
    for name in [name for name in os.environ if name.lower().startswith("npm_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("PATH", f"{root / 'mise'}:{REST}")
    monkeypatch.setenv("FAKE", str(root))
    monkeypatch.setenv("HOME", str(root / "home"))
    return root


@pytest.fixture
def working(root, monkeypatch):
    """A repository with the Lock and the lockfile that configure wrote."""
    (root / "repository/.gateseal").mkdir(parents=True)
    (root / "repository" / PATH).write_bytes(LOCKFILE)
    (root / "repository/gateseal.lock").write_text(json.dumps(LOCK))
    monkeypatch.chdir(root / "repository")
    return root / "repository"


@pytest.fixture
def final(root):
    """The one folder that an install of the lockfile gets in the test's home."""
    return lambda: only(root / "home/.cache/gateseal/npm" / MACHINE)


def only(parent):
    [folder] = parent.glob(f"node-{VERSION}/*")
    return folder


def start(monkeypatch, capfd, *arguments):
    """Start tool with arguments, and return its status and what it printed."""
    monkeypatch.setattr(sys, "argv", ["gateseal-npm-tool", "tool", "tool", *arguments])
    status = npm_tool.main()
    return status, *capfd.readouterr()


def calls(root, name="calls"):
    """Return the arguments of each call that a stand-in recorded."""
    text = (root / name).read_text() if (root / name).exists() else ""
    return [call.split("\n") for call in text.split("\n\n")[:-1]]


def test_the_first_use_installs_the_lockfile_once_and_starts_the_program(
    root, working, final, monkeypatch, capfd
):
    status, out, err = start(monkeypatch, capfd, "--check", "a file.ts")
    node, mise = root / "node/bin", root / "mise"
    path = f"{node}:{final() / 'node_modules/.bin'}:{mise}:{REST}"
    assert (status, out, err) == (0, f"{path}\n--check a file.ts\n", "")
    [call] = calls(root)
    new = call[2]
    assert new.startswith(f"{final()}.")
    assert call == [
        "ci",
        "--prefix",
        new,
        "--userconfig",
        f"{new}/.npmrc",
        "--globalconfig",
        f"{new}/.npmrc-global",
        "--cache",
        f"{new}/.npm",
        "--ignore-scripts",
        "--no-audit",
        "--no-update-notifier",
        "--logs-max=0",
    ]
    assert (root / "checked").read_text() == f"{new}\n"
    assert (root / "globalconfig").read_text() == ""
    assert [path.name for path in final().parent.iterdir()] == [final().name]
    assert sorted(path.name for path in final().iterdir()) == [
        "node_modules",
        "package-lock.json",
        "package.json",
    ]
    assert (final() / "package-lock.json").read_bytes() == LOCKFILE
    manifest = json.loads((final() / "package.json").read_text())
    assert manifest == {"dependencies": DEPENDENCIES}
    assert (root / "asked").read_text() == f"--no-config where {INSTALL}\n"
    assert start(monkeypatch, capfd)[0] == 0
    assert len(calls(root)) == 1


def test_the_hook_exits_with_the_status_of_the_tool(root, working, monkeypatch, capfd):
    monkeypatch.setenv("STATUS", "3")
    assert start(monkeypatch, capfd)[0] == 3


@pytest.mark.parametrize(
    "key", ["proxy", "https-proxy", "noproxy", "cafile", "registry"]
)
def test_npm_gets_an_allowed_setting_of_the_user_and_no_other(
    root, working, monkeypatch, capfd, key
):
    variable = f"npm_config_{key.replace('-', '_')}"
    others = {"npm_config_workspace": "app", "NPM_CONFIG_DRY_RUN": "true"}
    others |= {"npm_config_dry-run": "true", f"{variable}_": "x", variable: "x"}
    for name, value in others.items():
        monkeypatch.setenv(name, value)
    (root / "home").mkdir()
    (root / "home/.npmrc").write_bytes(
        f"workspace = app\n{key} = y\r\nglobal=true\n//registry.example/:_authToken=t"
        f"\n\tnoproxy[] = z\nx{key}=y\n{key}s=y\n#{key}=y\n".encode()
        + b"registry=\xff"
    )
    status, _, err = start(monkeypatch, capfd)
    assert (status, err) == (0, "")
    assert (root / "settings").read_text() == f"{variable}=x\n"
    assert (root / "userconfig").read_bytes() == (
        f"{key} = y\n//registry.example/:_authToken=t\n\tnoproxy[] = z\n".encode()
        + b"registry=\xff\n"
    )


def test_npm_keeps_an_allowed_variable_in_capitals(root, working, monkeypatch, capfd):
    monkeypatch.setenv("NPM_CONFIG_REGISTRY", "https://registry.example/")
    assert start(monkeypatch, capfd)[0] == 0
    assert (root / "settings").read_text() == (
        "NPM_CONFIG_REGISTRY=https://registry.example/\n"
    )


@pytest.mark.parametrize("variable", ["NPM_CONFIG_USERCONFIG", "npm_config_userconfig"])
def test_npm_reads_the_user_configuration_that_the_user_names(
    root, working, monkeypatch, capfd, variable
):
    (root / "home").mkdir()
    (root / "home/.npmrc").write_text("registry=https://home.example/\n")
    (root / "other.npmrc").write_text("registry=https://registry.example/\n")
    monkeypatch.setenv(variable, str(root / "other.npmrc"))
    status, _, err = start(monkeypatch, capfd)
    assert (status, err) == (0, "")
    assert (root / "settings").read_text() == ""
    assert (root / "userconfig").read_text() == "registry=https://registry.example/\n"


def test_the_variable_in_capitals_names_the_user_configuration_first(
    root, working, monkeypatch, capfd
):
    for name in ("upper", "lower"):
        (root / name).write_text(f"registry=https://{name}.example/\n")
    monkeypatch.setenv("NPM_CONFIG_USERCONFIG", str(root / "upper"))
    monkeypatch.setenv("npm_config_userconfig", str(root / "lower"))
    assert start(monkeypatch, capfd)[0] == 0
    assert (root / "userconfig").read_text() == "registry=https://upper.example/\n"


def test_npm_gets_the_real_path_of_a_cache_behind_a_symlink(
    root, working, monkeypatch, capfd
):
    (root / "real cache").mkdir()
    (root / "cache").symlink_to(root / "real cache")
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "cache"))
    assert start(monkeypatch, capfd)[0] == 0
    assert calls(root)[0][2].startswith(f"{root / 'real cache'}/")


def test_the_first_line_of_the_program_chooses_no_interpreter(
    root, working, monkeypatch, capfd
):
    (root / "first").write_text("#!/usr/bin/false")
    status, out, err = start(monkeypatch, capfd)
    assert (status, err) == (0, "")
    assert out.startswith(f"{root}/node/bin:")


NO_NODE = (
    f"mise has no install of Node {VERSION}, the pinned Node. "
    "Turn the auto-install of mise on.\n"
)

WRONG = "#!/bin/sh\necho 'a program of the PATH ran'; exit 9\n"


def test_without_the_node_that_mise_installed_nothing_installs(
    root, working, monkeypatch, capfd
):
    """mise exec with its auto-install off starts the command without a Node."""
    for name in ("node", "npm"):
        executable(root / "other" / name, WRONG)
    monkeypatch.setenv("PATH", f"{root / 'other'}:{os.environ['PATH']}")
    (root / "uninstalled").touch()
    assert start(monkeypatch, capfd) == (127, "", NO_NODE)
    assert calls(root) == []
    assert not (root / "home/.cache").exists()
    where = f"--no-config where {INSTALL}"
    asked = (root / "asked").read_text().splitlines()
    assert asked == [
        where,
        f"--no-config exec {REQUEST} -- {sys.executable} -I -S -c ",
        where,
    ]


@pytest.mark.parametrize(
    ("failure", "cause"),
    [("unreachable", "mise ERROR download failed"), ("silent", "exit code 3")],
)
def test_a_node_that_mise_cannot_install_stops_the_hook_with_the_cause(
    root, working, monkeypatch, capfd, failure, cause
):
    (root / "uninstalled").touch()
    (root / failure).touch()
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"mise could not install Node {VERSION}, the pinned Node: {cause}. "
        "Fix the cause, and the next start tries again.\n",
    )


def test_a_mise_that_cannot_start_stops_the_hook_and_names_the_node(
    root, working, monkeypatch, capfd
):
    (root / "mise/mise").chmod(0o644)
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"Node {VERSION}, the pinned Node, needs mise, and the mise on PATH cannot "
        "start: Permission denied. Install mise again.\n",
    )


def test_without_mise_the_hook_stops_and_names_the_node(
    root, working, monkeypatch, capfd
):
    monkeypatch.setenv("PATH", REST)
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"Node {VERSION}, the pinned Node, needs mise, and none is on PATH. "
        "Install mise.\n",
    )


def test_without_the_node_that_mise_installed_no_installed_program_starts(
    root, working, monkeypatch, capfd
):
    start(monkeypatch, capfd)
    (root / "uninstalled").touch()
    assert start(monkeypatch, capfd) == (127, "", NO_NODE)


def test_the_first_use_lets_mise_install_the_node(root, working, monkeypatch, capfd):
    (root / "uninstalled").touch()
    (root / "installs").touch()
    status, _, err = start(monkeypatch, capfd)
    assert (status, err) == (0, "")


def test_an_install_that_stopped_halfway_is_not_used(
    root, working, final, monkeypatch, capfd
):
    start(monkeypatch, capfd)
    half = final().with_name(f"{final().name}.1")
    final().rename(half)
    (half / "node_modules/tool-package/bin/tool.js").write_text("echo half")
    status, out, _ = start(monkeypatch, capfd)
    assert status == 0
    assert "half" not in out
    assert len(calls(root)) == 2


def test_two_first_uses_at_one_time_leave_the_install_that_came_first(
    root, working, final, monkeypatch, capfd
):
    (root / "raced").touch()
    status, out, err = start(monkeypatch, capfd)
    assert (status, out, err) == (0, "winner\n", "")
    assert [path.name for path in final().parent.iterdir()] == [final().name]


CANNOT_INSTALL = "npm cannot install the locked packages of tool.\n"


@pytest.mark.parametrize(
    ("failure", "said"),
    [("offline", "npm error\n"), ("incomplete", "npm installed no package.\n")],
)
def test_a_failed_install_stops_the_hook_and_leaves_no_folder(
    root, working, monkeypatch, capfd, failure, said
):
    (root / failure).touch()
    assert start(monkeypatch, capfd) == (127, "", said + CANNOT_INSTALL)
    assert list((root / "home/.cache/gateseal/npm" / MACHINE).glob("*/*")) == []


@pytest.mark.parametrize(
    ("cache", "folder"),
    [("{root}/xdg", "xdg"), ("xdg", "home/.cache"), ("", "home/.cache")],
    ids=["absolute", "relative", "empty"],
)
def test_the_install_goes_under_the_cache_folder_of_the_user(
    root, working, monkeypatch, capfd, cache, folder
):
    monkeypatch.setenv("XDG_CACHE_HOME", cache.format(root=root))
    assert start(monkeypatch, capfd)[0] == 0
    install = only(root / folder / "gateseal/npm" / MACHINE)
    assert (install / "node_modules/tool-package/bin/tool.js").is_file()
    assert not (working / "xdg").exists()


def test_a_cache_folder_that_is_a_file_stops_the_hook_and_names_it(
    root, working, monkeypatch, capfd
):
    (root / "xdg").write_text("")
    monkeypatch.setenv("XDG_CACHE_HOME", str(root / "xdg"))
    parent = root / "xdg/gateseal/npm" / MACHINE / f"node-{VERSION}"
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"gateseal cannot write in {parent}: Not a directory. Make the cache "
        "folder writable and give it space, or set XDG_CACHE_HOME to another one.\n",
    )


def test_a_write_that_fails_in_the_cache_stops_the_hook_and_names_the_cause(
    root, working, monkeypatch, capfd
):
    (root / "full").write_text("")
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(root / "full/new"))
    parent = root / "home/.cache/gateseal/npm" / MACHINE / f"node-{VERSION}"
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"gateseal cannot write in {parent}: Not a directory. Make the cache "
        "folder writable and give it space, or set XDG_CACHE_HOME to another one.\n",
    )


def test_a_settings_file_that_npm_cannot_get_stops_the_hook_and_names_the_folder(
    root, working, monkeypatch, capfd
):
    new = root / "new"
    (new / ".npmrc").mkdir(parents=True)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **_: str(new))
    parent = root / "home/.cache/gateseal/npm" / MACHINE / f"node-{VERSION}"
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"gateseal cannot write in {parent}: Is a directory. Make the cache "
        "folder writable and give it space, or set XDG_CACHE_HOME to another one.\n",
    )


def test_each_node_and_each_lockfile_has_its_own_install_folder(
    root, working, monkeypatch, capfd
):
    start(monkeypatch, capfd)
    other = json.loads(LOCKFILE)
    other["packages"][""]["dependencies"]["tool-package"] = "1.0.1"
    (working / PATH).write_bytes(canonical(other))
    lock = json.loads(json.dumps(LOCK))
    lock["outputs"][PATH]["sha256"] = sha(canonical(other))
    (working / "gateseal.lock").write_text(json.dumps(lock))
    start(monkeypatch, capfd)
    lock["native_artifacts"]["node"]["version"] = "24.22.2"
    (working / "gateseal.lock").write_text(json.dumps(lock))
    executable(root / "mise/mise", f"#!/bin/sh\necho '{root}/node'\n")
    start(monkeypatch, capfd)
    parent = root / "home/.cache/gateseal/npm" / MACHINE
    [older] = [path.name for path in (parent / "node-24.22.2").iterdir()]
    keys = {path.name for path in (parent / f"node-{VERSION}").iterdir()}
    assert len(keys) == 2 and older in keys
    assert all(len(key) == 16 for key in keys)


def test_a_lockfile_with_crlf_line_ends_is_the_one_that_the_lock_records(
    root, working, final, monkeypatch, capfd
):
    (working / PATH).write_bytes(LOCKFILE.replace(b"\n", b"\r\n"))
    assert LOCKFILE.count(b"\n") > 1
    status, _, err = start(monkeypatch, capfd)
    assert (status, err) == (0, "")
    assert (final() / "package-lock.json").read_bytes() == LOCKFILE


def test_the_install_gets_the_bytes_that_the_lock_records_in_any_locale(
    root, working, final
):
    """Python writes text in the encoding of the locale, which can lack é."""
    lockfile = canonical(json.loads(LOCKFILE) | {"name": "outil-é"})
    (working / PATH).write_bytes(lockfile)
    (working / "gateseal.lock").write_text(
        json.dumps(LOCK | {"outputs": {PATH: {"sha256": sha(lockfile)}}})
    )
    hook = [sys.executable, "-X", "utf8=0", "-m", "gateseal.npm_tool", "tool", "tool"]
    env = os.environ | {"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"}
    ran = subprocess.run(hook, env=env, capture_output=True, text=True)
    assert (ran.returncode, ran.stderr) == (0, "")
    assert (final() / "package-lock.json").read_bytes() == lockfile


def test_a_node_in_a_folder_with_any_name_starts_in_any_locale(root, working):
    """mise where prints the folder's bytes, which the locale may not decode."""
    (root / "node").rename(root / "nodé")
    executable(root / "mise/mise", f"#!/bin/sh\necho '{root}/nodé'\n")
    hook = [sys.executable, "-X", "utf8=0", "-m", "gateseal.npm_tool", "tool", "tool"]
    env = os.environ | {"LC_ALL": "C", "PYTHONCOERCECLOCALE": "0"}
    ran = subprocess.run(hook, env=env, capture_output=True)
    assert (ran.returncode, ran.stderr) == (0, b"")


@pytest.mark.parametrize("used", [False, True])
def test_a_node_that_lost_its_program_stops_the_hook_and_names_it(
    root, working, monkeypatch, capfd, used
):
    """mise where still names the folder, so only the start finds the loss."""
    if used:
        start(monkeypatch, capfd)
    (root / "node/bin/node").unlink()
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"The pinned Node in {root / 'node'} cannot start: No such file or "
        "directory. Delete the folder, and the next start lets mise install it "
        "again.\n",
    )


def test_a_lockfile_other_than_the_lock_s_starts_nothing(
    root, working, monkeypatch, capfd
):
    (working / PATH).write_bytes(LOCKFILE + b"\n")
    assert start(monkeypatch, capfd) == (
        127,
        "",
        f"{PATH} is not the file that gateseal.lock records. Run gateseal configure.\n",
    )
    assert not (root / "asked").exists()


NO_TOOL = (
    f"gateseal.lock does not record the tool tool and its {PATH}. Run gateseal "
    "configure.\n"
)
NO_NODE_RECORD = (
    "gateseal.lock does not record the pinned Node. Run gateseal configure.\n"
)


def without(*keys):
    lock = json.loads(json.dumps(LOCK))
    found = lock
    for key in keys[:-1]:
        found = found[key]
    del found[keys[-1]]
    return json.dumps(lock)


def node_with(field, value):
    """Return the Lock with one field of its Node record set to value."""
    lock = json.loads(json.dumps(LOCK))
    node = lock["native_artifacts"]["node"]
    record = node if field == "version" else next(iter(node["platforms"].values()))
    record[field] = value
    return json.dumps(lock)


@pytest.mark.parametrize(
    ("lock", "said"),
    [
        (without("components", "tool"), NO_TOOL),
        (without("components", "tool", "package"), NO_TOOL),
        (without("outputs", PATH), NO_TOOL),
        (without("outputs", PATH, "sha256"), NO_TOOL),
        (json.dumps(LOCK | {"components": []}), NO_TOOL),
        (without("native_artifacts", "node"), NO_NODE_RECORD),
        (without("native_artifacts", "node", "version"), NO_NODE_RECORD),
        (without("native_artifacts", "node", "platforms"), NO_NODE_RECORD),
        (json.dumps(LOCK | {"native_artifacts": {"node": []}}), NO_NODE_RECORD),
        (
            json.dumps(
                LOCK | {"native_artifacts": {"node": {"version": 1, "platforms": 2}}}
            ),
            NO_NODE_RECORD,
        ),
        (node_with("version", 24), NO_NODE_RECORD),
        (node_with("url", None), NO_NODE_RECORD),
        (node_with("sha256", None), NO_NODE_RECORD),
        ("{", NO_NODE_RECORD),
        (None, NO_NODE_RECORD),
    ],
)
def test_a_lock_without_what_the_hook_needs_starts_nothing(
    root, working, monkeypatch, capfd, lock, said
):
    if lock is None:
        (working / "gateseal.lock").unlink()
    else:
        (working / "gateseal.lock").write_text(lock)
    assert start(monkeypatch, capfd) == (127, "", said)
    assert not (root / "asked").exists()


@pytest.mark.parametrize("lockfile", [None, b"{", b'{"packages": {}}', LOCKFILE])
def test_a_lockfile_without_the_program_starts_nothing(
    root, working, monkeypatch, capfd, lockfile
):
    """The last case is the lockfile of the Lock, which links no other program."""
    if lockfile is None:
        (working / PATH).unlink()
    else:
        (working / PATH).write_bytes(lockfile)
    monkeypatch.setattr(sys, "argv", ["gateseal-npm-tool", "tool", "other"])
    assert (npm_tool.main(), *capfd.readouterr()) == (127, "", NO_TOOL)
    assert not (root / "asked").exists()


@pytest.mark.parametrize("arguments", [[], ["tool"]])
def test_the_hook_needs_the_names_of_a_tool_and_its_executable(
    root, working, monkeypatch, capfd, arguments
):
    monkeypatch.setattr(sys, "argv", ["gateseal-npm-tool", *arguments])
    assert (npm_tool.main(), *capfd.readouterr()) == (
        127,
        "",
        "gateseal-npm-tool needs a tool's name and its executable's name. Run "
        "gateseal configure.\n",
    )


def loaded(module):
    """Return the modules that a new Python holds once it imported module."""
    code = f"import sys, {module}; print(*sorted(sys.modules))"
    return set(subprocess.check_output([sys.executable, "-c", code], text=True).split())


def test_the_hook_s_module_loads_little_at_a_start():
    """A start of a hook must not pay for gateseal's other modules."""
    added = loaded("gateseal.npm_tool") - loaded("gateseal")
    assert "gateseal.npm_tool" in added
    assert not [name for name in added - {"gateseal.npm_tool"} if "gateseal" in name]
    assert not added & {"typing", "pathlib", "dataclasses", "shutil", "tempfile"}
