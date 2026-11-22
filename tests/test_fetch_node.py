import os
from types import SimpleNamespace

import pytest
from conftest import TRACKED
from gateseal import fetch, lock, setup
from gateseal.platforms import machine
from gateseal.tools.npm_package import NpmTool
from gateseal.worktree import locate_worktree, preparation_directory
from installation import success
from stand_ins import Installer, configure


@pytest.fixture(scope="module")
def typescript(tmp_path_factory):
    directory = tmp_path_factory.mktemp("fetch node")
    root = directory / "clone"
    success(["git", "init", "--quiet", "--initial-branch=main", root], directory)
    for key, value in (("user.name", "gateseal test"), ("user.email", "t@x.invalid")):
        success(["git", "config", key, value], root)
    worktree = locate_worktree(root)
    configure(worktree, ("hygiene", "typescript"))
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "--quiet", "-m", "Adopt"], root)
    return lock.load(worktree.root, preparation_directory(worktree))


@pytest.fixture
def installers(tmp_path, monkeypatch):
    mise = Installer(tmp_path / "mise bin" / "mise")
    pre_commit = Installer(tmp_path / "pre-commit" / "python")
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(mise.path.parent), os.environ["PATH"]])
    )
    monkeypatch.setattr(setup, "require_pre_commit", lambda: str(pre_commit.path))
    return SimpleNamespace(mise=mise, pre_commit=pre_commit)


def test_fetch_installs_the_pinned_node_once_for_all_tools_on_node(
    typescript, installers
):
    tools = [t for t in typescript.selection.tools if isinstance(t, NpmTool)]
    assert len(tools) >= 2
    node = tools[0].node
    entries = fetch.fetch(typescript, setup.require_pre_commit())
    request = fetch.mise_request("node", node.version, node.platforms)
    assert installers.mise.calls() == [f"--no-config install {request}"]
    url = node.platforms[machine()].url
    assert entries[0]["message"] == f"mise installed node {node.version} from {url}."
