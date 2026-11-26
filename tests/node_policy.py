"""A team policy whose docs capability runs Prettier from npm on the policy's Node.

A fake upstream serves nodejs.org's release files and the team policy's
other tools from dictionaries, and resolves npm lockfiles, where gateseal
policy lock and add would reach the network.
"""

import hashlib
from base64 import b64encode

import pytest
from gateseal import authoring
from gateseal.tools.model import MissingUpstream
from test_team_tools import Upstream as TeamUpstream
from test_team_tools import team

VERSION = "24.22.3"
DIST = f"https://nodejs.org/dist/v{VERSION}/"
LINUX = f"node-v{VERSION}-linux-x64.tar.gz"
MACOS = f"node-v{VERSION}-darwin-arm64.tar.gz"
SHASUMS = DIST + "SHASUMS256.txt"

PRETTIER = "3.10.1"
PACKAGES = {"prettier-plugin-toml": "2.0.6", "@prettier/plugin-xml": "3.4.2"}
"""The tool's packages, as policy.toml lists them: not in name order."""

DEFINITION = """\
kind = "npm-package"
stages = ["commit", "run", "ci"]
scope = "files"

[source]
package = "prettier"

[executable]
name = "prettier"

[configuration]
format = "toml"

[hooks.prettier-docs]
name = "prettier docs"
args = [
  "--config", "{configuration}",
  "--no-editorconfig", "--ignore-path", "/dev/null",
  "--write", "--ignore-unknown", "--",
]
types_or = ["markdown", "yaml"]
output = "plain"
"""

RULES = 'proseWrap = "always"\n'

PINS = "".join(f'"{name}" = "{value}"\n' for name, value in PACKAGES.items())

TABLE = f"""\
[capabilities.docs.tools.prettier-docs]
configuration = "rules/prettier-docs.toml"
pin = "{PRETTIER}"
reviewed = ["{PRETTIER}"]

[capabilities.docs.tools.prettier-docs.packages]
{PINS}
[capabilities.docs.choices]
exclude = []
"""

POLICY = f"""\
schema = 5
release = "team-1"
minimum_compiler = "0.2.0"
node = "{VERSION}"

[capabilities.hygiene.tools.pre-commit-hooks]
hook_ids = ["check-yaml"]
pin = "6.0.0"
reviewed = ["6.0.0"]

{TABLE}"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def node_files(version=VERSION):
    """Return nodejs.org's files of a Node release: its archives and SHASUMS256.txt.

    The listing names a file gateseal never asks for too, as the real one does.
    """
    base = f"https://nodejs.org/dist/v{version}/"
    names = (
        f"node-v{version}-darwin-arm64.tar.gz",
        f"node-v{version}-linux-x64.tar.gz",
    )
    files = {base + name: f"{name} bytes".encode() for name in names}
    listing = "".join(
        f"{sha(f'{name} bytes'.encode())}  {name}\n"
        for name in (*names, f"node-v{version}-win-x64.zip")
    )
    return {base + "SHASUMS256.txt": listing.encode()} | files


def node_record(version=VERSION):
    """Return the Policy Lock record that gateseal policy lock writes for version."""
    base = f"https://nodejs.org/dist/v{version}/"
    return {
        "platforms": {
            platform: {
                "sha256": sha(f"node-v{version}-{name}.tar.gz bytes".encode()),
                "url": f"{base}node-v{version}-{name}.tar.gz",
            }
            for platform, name in (
                ("linux-x64", "linux-x64"),
                ("macos-arm64", "darwin-arm64"),
            )
        },
        "version": version,
    }


PROGRAMS = {"typescript": "tsc"}
"""The program of each package that links none named after the package."""


def lockfile(dependencies):
    """Return the lockfile that the fake npm resolves for dependencies.

    It installs each package alone at its version, with a made-up integrity
    hash, and each links one program.
    """
    packages = {
        f"node_modules/{name}": {
            "bin": {PROGRAMS.get(name, name.rpartition("/")[2]): "bin/cli.js"},
            "integrity": f"sha512-{b64encode(f'{name}@{version}'.encode()).decode()}",
            "version": version,
        }
        for name, version in dependencies.items()
    }
    return {
        "lockfileVersion": 3,
        "packages": {"": {"dependencies": dict(dependencies)}} | packages,
        "requires": True,
    }


class Releases:
    """Tags, nodejs.org and npm, recording each URL and package set a lock asks for.

    npm lacks each name@version in missing. A program that evaluate runs prints
    presets: for each package that the argument names, its configs, each preset
    of presets under flat/<name> and <name>, and each requested one it has.
    """

    def __init__(self, files, tags=None):
        self.files = dict(files)
        self.tags = dict(tags or {})
        self.downloaded = []
        self.resolved = []
        self.missing = set()
        self.evaluated = []
        self.presets = {}

    def tagged(self, repository, tag):
        return self.tags.get((repository, tag))

    def download(self, url):
        self.downloaded.append(url)
        return self.files.get(url)

    def lockfile(self, dependencies, tool, version):
        self.resolved.append(dict(dependencies))
        lacking = {f"{name}@{v}" for name, v in dependencies.items()} & self.missing
        if lacking:
            raise MissingUpstream(
                "upstream_release_missing",
                f"npm cannot resolve the packages of {tool} {version}: "
                f"No matching version found for {min(lacking)}.",
                tool,
                version,
            )
        return lockfile(dependencies)

    def evaluate(self, lockfile, program, argument, tool, version):
        self.evaluated.append((program.name, tool, version, argument))
        return {
            package: {
                "configs": [
                    key
                    for name in self.presets[package]
                    for key in (f"flat/{name}", name)
                ],
                "presets": {
                    name: self.presets[package][name]
                    for name in names
                    if name in self.presets[package]
                },
            }
            for package, names in argument["presets"].items()
        }


@pytest.fixture
def releases(monkeypatch):
    """Serve the team policy's tags and Node, and resolve its npm packages."""
    hooks = TeamUpstream()
    fake = Releases(hooks.files | node_files(), hooks.tags)
    monkeypatch.setattr(authoring, "Releases", lambda git, scratch, rerun: fake)
    return fake


def docs(tmp_path, policy=POLICY, definition=DEFINITION, rules=RULES):
    """Write the team policy with Prettier for docs, and only the files it names."""
    files = {
        "rules/gitleaks.toml": None,
        "tools/yamllint.toml": None,
        "tools/gitleaks.toml": None,
        "rules/prettier-docs.toml": rules,
        "tools/prettier-docs.toml": definition,
    }
    return team(tmp_path, policy, files)
