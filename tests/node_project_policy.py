"""A team policy whose type checks read the repository's Node project.

Its typescript-types capability runs the Built-in Tool tsc, at a push and in
the gate, and its vue-types capability a team-defined vue-tsc at a commit and
in the gate. docs runs node_policy's Prettier, an npm-package tool that reads
no project. A fake upstream serves nodejs.org, npm and the policy's other
tools, where gateseal policy lock would reach the network.
"""

import shlex

import pytest
from gateseal import authoring, lock
from gateseal.tools.node import Node
from gateseal.tools.package_managers import PNPM
from node_policy import (
    DEFINITION,
    RULES,
    TABLE,
    VERSION,
    Releases,
    node_files,
)
from team_policy import URL, git
from test_lock_configure import configure, worktree
from test_team_tools import Upstream as TeamUpstream
from test_team_tools import team

TYPESCRIPT = "7.0.2"
VUE_TSC = "3.1.4"

VUE_TSC_DEFINITION = """\
kind = "node-project"
stages = ["commit", "run", "ci"]
scope = "project"

[source]
package = "vue-tsc"

[executable]
name = "vue-tsc"

[hooks.vue-tsc]
name = "vue-tsc"
args = ["--noEmit"]
output = "plain"
"""

HEADER = f"""\
schema = 5
release = "team-1"
minimum_compiler = "0.2.0"
node = "{VERSION}"

[capabilities.hygiene.tools.pre-commit-hooks]
hook_ids = ["check-yaml"]
pin = "6.0.0"
reviewed = ["6.0.0"]
"""

POLICY = f"""\
{HEADER}
[capabilities.typescript-types.tools.tsc]
pin = "{TYPESCRIPT}"
reviewed = ["{TYPESCRIPT}"]

[capabilities.vue-types.tools.vue-tsc]
pin = "{VUE_TSC}"
reviewed = ["{VUE_TSC}"]

{TABLE}"""


def install_arguments(node: Node) -> list[str]:
    """Return the arguments of gateseal's install hook on node: npm's, then pnpm's."""
    pnpm = node.launch(PNPM.tree, None, "pnpm")
    return [f"npm={shlex.join(node.included('npm'))}", f"pnpm={shlex.join(pnpm)}"]


def upstream():
    """Return a fake upstream with every release that the team policy names."""
    hooks = TeamUpstream()
    return Releases(hooks.files | node_files(), hooks.tags)


@pytest.fixture
def registry(monkeypatch):
    """Answer gateseal policy lock from upstream()."""
    fake = upstream()
    monkeypatch.setattr(authoring, "Releases", lambda git, scratch, rerun: fake)
    return fake


def typed(tmp_path, policy=POLICY):
    """Write the team policy into tmp_path/policy, with only the files it names."""
    files = {
        "rules/gitleaks.toml": None,
        "tools/yamllint.toml": None,
        "tools/gitleaks.toml": None,
        "rules/prettier-docs.toml": RULES,
        "tools/prettier-docs.toml": DEFINITION,
        "tools/vue-tsc.toml": VUE_TSC_DEFINITION,
    }
    return team(tmp_path, policy, files)


def publish(origin, tag, policy=POLICY):
    """Lock policy offline and commit it as origin's release tag."""
    root = typed(origin, policy)
    (root / "policy.lock").unlink(missing_ok=True)
    with pytest.MonkeyPatch.context() as monkeypatch:
        fake = upstream()
        monkeypatch.setattr(authoring, "Releases", lambda git, scratch, rerun: fake)
        authoring.lock(root)
    git(origin, "add", "-A")
    git(origin, "commit", "--allow-empty", "-m", f"Release {tag}")
    git(origin, "tag", "-a", "-m", tag, tag)


def new_origin(tmp_path_factory, name):
    origin = tmp_path_factory.mktemp(name) / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    return origin


def reach(monkeypatch, origin):
    """Make Git fetch the team URL from origin."""
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", URL)


def project(tmp_path, *names):
    """Return an empty worktree that holds names, each an empty JSON object."""
    root = worktree(tmp_path)
    for name in names:
        (root / name).write_text("{}")
    return root


def adopt(root, *capabilities, tag="v1"):
    """Configure root with the team policy's release tag."""
    request = lock.PolicyRequest(URL, None, "policy", tag)
    return configure(root, list(capabilities), request)
