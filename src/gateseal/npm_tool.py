"""The gateseal-npm-tool hook: start a tool from npm on the pinned Node.

A hook of a tool from npm is gateseal-npm-tool TOOL EXECUTABLE ARGS. The Lock
in the working folder says the rest: native_artifacts.node holds the URL and
the SHA-256 of Node's archive for each platform, components.TOOL the tool's
package, and outputs the SHA-256 of .gateseal/TOOL.package-lock.json, npm's
lockfile of the tool. The hook asks mise for that Node, installs the lockfile
once into a folder of the user's cache, with no install script, and starts
the program that the package links as EXECUTABLE on that Node with ARGS.

The hook stops with status 127 when it cannot start the tool, so the tool's
own exit codes keep their meaning. The pinned Node starts the program by its
path, so neither its first line nor a node_modules/.bin chooses what runs.

gateseal's install hook and gateseal policy lock start npm and pnpm through
the same functions, so npm reads the same settings wherever gateseal starts it.

Each start of a hook loads this module, so it imports only what a start
needs, and only from the standard library.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys

TYPE_CHECKING = False  # pytestmut: skip[bool.literal] -- only start time differs
if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

LOCK = "gateseal.lock"
"""The Lock. managed_files takes the name from here."""

LOCKFILE = ".gateseal/{tool}.package-lock.json"
"""The owned output that holds npm's lockfile of a tool."""

NODE = "node"
"""The name of the policy's Node in mise's request and in the Lock."""

STOPPED = 127
"""The status of a hook that could not start its tool."""

USER_SETTING = re.compile(
    rb"\s*(proxy|https-proxy|noproxy(\[\])?|cafile|registry|//[^=]+)\s*="
)
"""A line of the user's .npmrc that npm may read: a proxy, a CA file, a
registry, or a registry's own setting, such as its token."""

VARIABLE = re.compile("npm_config_(proxy|https_proxy|noproxy|cafile|registry)", re.I)
"""An npm_config_ variable that npm may read."""

CHECK_INSTALL = """
const [dir] = process.argv.slice(1);
const read = (name) => JSON.parse(fs.readFileSync(path.join(dir, name), "utf8"));
const { packages } = read("node_modules/.package-lock.json");
const npm = path.join(process.execPath, "../../lib/node_modules/npm");
const checks = require(path.join(npm, "node_modules/npm-install-checks"));
for (const [name, entry] of Object.entries(read("package-lock.json").packages)) {
  try { checks.checkPlatform(entry); } catch { continue; }
  if ((entry.os || entry.cpu || entry.libc) && !packages[name]) {
    console.error(`npm installed no ${name}.`);
    process.exit(1);
  }
}
"""
"""node -e CHECK_INSTALL DIR: fail unless the install DIR is complete.

npm's record of the install must list each package made for this platform:
npm ci exits 0 without one that it cannot download or whose hash differs.
"""


class Stopped(Exception):
    """The program cannot start. The text says why, and the next step."""


def mise_names(
    name: str, version: str, archives: Mapping[str, tuple[str, str]]
) -> tuple[str, str]:
    """Return mise's request for a tool and the name of the install it makes.

    archives holds each platform's URL and SHA-256. mise reads the option
    platforms_<os>_<arch>_<name> as the nested platforms.<os>-<arch>.<name> of
    a mise file. A URL, a checksum and a version hold no comma or bracket,
    which would end an option. mise installs a request once under its tool
    name and version and never reads its options again, so the tool name ends
    in a digest of them. mise where prints the folder of that install, and
    fails when mise has not installed it, so the folder holds only an archive
    that mise checked.
    """
    options = ",".join(
        f"platforms_{platform.replace('-', '_')}_{option}={value}"
        for platform, (url, sha256) in sorted(archives.items())
        for option, value in (("url", url), ("checksum", "sha256:" + sha256))
    )
    tool = f"http:{name}-{hashlib.sha256(options.encode()).hexdigest()[:12]}"
    return f"{tool}[{options}]@{version}", f"{tool}@{version}"


def pinned_node(version: str, archives: Mapping[str, tuple[str, str]]) -> str:
    """Return the bin folder of the Node that mise installed from archives.

    mise exec installs a missing Node unless the user turned its auto-install
    off. Only mise where then says whether the install exists: without it,
    mise exec starts whatever node the PATH holds.
    """
    request, install = mise_names(NODE, version, archives)
    found, installed = _mise_install(version, request, install)
    if found.returncode and installed and installed.returncode:
        lines = installed.stderr.decode(errors="replace").strip().splitlines()
        errors = [line for line in lines if "ERROR" in line and "--verbose" not in line]
        cause = " ".join(errors) or f"exit code {installed.returncode}"
        raise Stopped(
            f"mise could not install Node {version}, the pinned Node: {cause}. "
            "Fix the cause, and the next start tries again."
        )
    if found.returncode:
        raise Stopped(
            f"mise has no install of Node {version}, the pinned Node. "
            "Turn the auto-install of mise on."
        )
    return os.path.join(os.fsdecode(found.stdout.strip()), "bin")


def _mise_install(
    version: str, request: str, install: str
) -> tuple[
    subprocess.CompletedProcess[bytes], subprocess.CompletedProcess[bytes] | None
]:
    """Return mise where's answer for install, and mise exec's if it ran first."""
    where = ["mise", "--no-config", "where", install]
    installed = None
    try:
        found = subprocess.run(where, capture_output=True)
        if found.returncode:
            nothing = [sys.executable, "-I", "-S", "-c", ""]
            exec_ = ["mise", "--no-config", "exec", request, "--", *nothing]
            installed = subprocess.run(exec_, capture_output=True)
            found = subprocess.run(where, capture_output=True)
    except FileNotFoundError:
        raise Stopped(
            f"Node {version}, the pinned Node, needs mise, and none is on PATH. "
            "Install mise."
        ) from None
    except OSError as error:
        raise Stopped(
            f"Node {version}, the pinned Node, needs mise, and the mise on PATH "
            f"cannot start: {error.strerror}. Install mise again."
        ) from None
    return found, installed


def on_node(
    node: str, program: str, args: Sequence[str] = (), install: str | None = None
) -> tuple[list[str], dict[str, str]]:
    """Return the command and the environment that start program on node.

    node is pinned_node's folder, which comes first on PATH. The programs
    that the packages of install link follow it.
    """
    folders = [node]
    if install is not None:
        folders.append(os.path.join(install, "node_modules", ".bin"))
    path = os.pathsep.join([*folders, os.environ.get("PATH", "")])
    command = [os.path.join(node, "node"), program, *args]
    return command, {**os.environ, "PATH": path}


def start(
    command: Sequence[str],
    env: Mapping[str, str],
    cwd: str | None = None,
    stdout: int | None = None,
) -> int:
    """Run a command of on_node, and return its exit status.

    mise can name an install of Node that lost a file, such as bin/node. Then
    the command cannot start, and the hook stops: a status of its own would
    read as the tool's, such as a finding of ESLint.
    """
    try:
        return subprocess.run(command, cwd=cwd, env=env, stdout=stdout).returncode
    except OSError as error:
        folder = os.path.dirname(os.path.dirname(command[0]))
        raise Stopped(
            f"The pinned Node in {folder} cannot start: {error.strerror}. "
            "Delete the folder, and the next start lets mise install it again."
        ) from None


def npm_in(
    node: str, folder: str, args: Sequence[str]
) -> tuple[list[str], dict[str, str]]:
    """Return the command and the environment of npm ARGS on folder's package.json.

    npm reads only the user's proxy, CA and registry, from .npmrc and npm_config_
    variables, and a registry's own .npmrc lines such as its token. A copy of
    those lines in folder is its user configuration, and an empty file there its
    global one. No install script runs. npm gets folder's real path: through a
    symlink, such as macOS's /var, npm writes broken lockfile paths.
    """
    real = os.path.realpath(folder)
    user = (
        os.environ.get("NPM_CONFIG_USERCONFIG")
        or os.environ.get("npm_config_userconfig")
        or os.path.join(os.path.expanduser("~"), ".npmrc")
    )
    try:
        with open(user, "rb") as file:
            lines = [line for line in file.read().splitlines() if _allowed(line)]
    except OSError:
        lines = []
    settings = {"userconfig": ".npmrc", "globalconfig": ".npmrc-global"}
    with open(os.path.join(real, settings["userconfig"]), "wb") as file:
        file.writelines(line + b"\n" for line in lines)
    with open(os.path.join(real, settings["globalconfig"]), "wb"):
        pass
    command, env = on_node(node, os.path.join(node, "npm"), [*args, "--prefix", real])
    for name, file in (*settings.items(), ("cache", ".npm")):
        command += [f"--{name}", os.path.join(real, file)]
    command += ["--ignore-scripts", "--no-audit", "--no-update-notifier"]
    command += ["--logs-max=0"]
    kept = {
        name: value
        for name, value in env.items()
        if not name.lower().startswith("npm_config_") or VARIABLE.fullmatch(name)
    }
    return command, kept


def _allowed(line: bytes) -> bool:
    return USER_SETTING.match(line) is not None


def install(node: str, version: str, name: str, lockfile: str) -> str:
    """Return the folder that holds the packages of lockfile, installed once.

    name says whose packages they are, and version is node's. Each Node and
    each lockfile has its own folder in the user's cache. npm ci installs
    into a new folder beside it, which is renamed once CHECK_INSTALL passes, so
    no half install is used. A rename fails only when another first use took
    the name, and that install serves.
    """
    cache = os.environ.get("XDG_CACHE_HOME", "")
    if not os.path.isabs(cache):
        cache = os.path.join(os.path.expanduser("~"), ".cache")
    with open(__file__, "rb") as file:
        key = hashlib.sha256(file.read() + lockfile.encode()).hexdigest()[:16]
    machine = f"{platform.system()}-{platform.machine()}"
    parent = os.path.join(cache, "gateseal", "npm", machine, f"node-{version}")
    folder = os.path.join(parent, key)
    if os.path.exists(folder):
        return folder
    import shutil
    import tempfile

    try:
        os.makedirs(parent, exist_ok=True)
        new = tempfile.mkdtemp(prefix=f"{key}.", dir=parent)
    except OSError as error:
        raise _unwritable(parent, error) from None
    try:
        dependencies = json.loads(lockfile)["packages"][""]["dependencies"]
        try:
            for file, text in (
                ("package-lock.json", lockfile),
                ("package.json", json.dumps({"dependencies": dependencies})),
            ):
                with open(os.path.join(new, file), "wb") as out:
                    out.write(text.encode())
            command, env = npm_in(node, new, ["ci"])
        except OSError as error:
            raise _unwritable(parent, error) from None
        failed = start(command, env, stdout=subprocess.DEVNULL)
        shutil.rmtree(os.path.join(new, ".npm"), ignore_errors=True)
        os.remove(os.path.join(new, ".npmrc"))
        os.remove(os.path.join(new, ".npmrc-global"))
        check, env = on_node(node, "-e", [CHECK_INSTALL, new])
        if failed or start(check, env):
            raise Stopped(f"npm cannot install the locked packages of {name}.")
        try:
            os.rename(new, folder)
        except OSError:
            pass
    finally:
        shutil.rmtree(new, ignore_errors=True)
    return folder


def _unwritable(parent: str, error: OSError) -> Stopped:
    return Stopped(
        f"gateseal cannot write in {parent}: {error.strerror}. Make the cache "
        "folder writable and give it space, or set XDG_CACHE_HOME to another one."
    )


def locked_node() -> tuple[str, dict[str, tuple[str, str]]]:
    """Return the version and the archives of the Node that the Lock records."""
    return _node(_lock())


def _lock() -> Any:
    try:
        with open(LOCK, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError):
        raise _unrecorded("the pinned Node") from None


def _node(lock: Any) -> tuple[str, dict[str, tuple[str, str]]]:
    try:
        node = lock["native_artifacts"][NODE]
        archives = {
            name: (archive["url"], archive["sha256"])
            for name, archive in node["platforms"].items()
        }
        texts = [
            node["version"],
            *(text for pair in archives.values() for text in pair),
        ]
        if not all(isinstance(text, str) for text in texts):
            raise TypeError
        return node["version"], archives
    except (KeyError, TypeError, AttributeError):
        raise _unrecorded("the pinned Node") from None


def _unrecorded(subject: str) -> Stopped:
    return Stopped(f"{LOCK} does not record {subject}. Run gateseal configure.")


def tool(lock: Any, name: str, executable: str) -> tuple[str, str]:
    """Return npm's lockfile of the tool name and the program of executable in it.

    The lockfile must be the one whose SHA-256 the Lock records, whatever its
    line ends, so the hook installs no other packages than the reviewed ones.
    """
    path = LOCKFILE.format(tool=name)
    try:
        with open(path, encoding="utf-8", newline="") as file:
            lockfile = file.read().replace("\r\n", "\n")
        pinned = lock["outputs"][path]["sha256"]
        package = f"node_modules/{lock['components'][name]['package']}"
        program = json.loads(lockfile)["packages"][package]["bin"][executable]
    except (OSError, ValueError, KeyError, TypeError):
        raise _unrecorded(f"the tool {name} and its {path}") from None
    if hashlib.sha256(lockfile.encode()).hexdigest() != pinned:
        raise Stopped(
            f"{path} is not the file that {LOCK} records. Run gateseal configure."
        )
    return lockfile, f"{package}/{program}"


def run(arguments: Sequence[str]) -> int:
    """Start the tool that arguments name, and return its exit status."""
    if len(arguments) < 2:
        raise Stopped(
            "gateseal-npm-tool needs a tool's name and its executable's name. "
            "Run gateseal configure."
        )
    name, executable, *args = arguments
    lock = _lock()
    lockfile, program = tool(lock, name, executable)
    version, archives = _node(lock)
    node = pinned_node(version, archives)
    folder = install(node, version, name, lockfile)
    command, env = on_node(node, os.path.join(folder, program), args, folder)
    return start(command, env)


def main() -> int:
    try:
        return run(sys.argv[1:])
    except Stopped as stopped:
        print(stopped, file=sys.stderr)
        return STOPPED


if __name__ == "__main__":
    sys.exit(main())
