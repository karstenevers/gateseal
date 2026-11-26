"""gateseal upgrade needs the Node project of a node-project tool it adds.

A team policy's release plain offers hygiene alone. Its release typed adds
the Built-in Tool tsc to hygiene, so a move to it runs a tool in the
repository's Node project, which the consumer must hold before any write.
"""

import json

import pytest
from gateseal import lock, upgrade
from node_project_policy import HEADER, TYPESCRIPT, new_origin, publish, reach
from team_policy import URL
from test_upgrade_worktree import configure, refused, request

PLAIN = "\n".join(line for line in HEADER.splitlines() if not line.startswith("node"))
TYPED = f"""\
{HEADER}
[capabilities.hygiene.tools.tsc]
pin = "{TYPESCRIPT}"
reviewed = ["{TYPESCRIPT}"]
"""


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    origin = new_origin(tmp_path_factory, "node project upgrade")
    publish(origin, "plain", PLAIN + "\n")
    publish(origin, "typed", TYPED)
    return origin


def test_a_move_to_a_node_project_tool_needs_the_project_first(
    consumer, origin, monkeypatch
):
    reach(monkeypatch, origin)
    plain = lock.PolicyRequest(URL, None, "policy", "plain")
    worktree = configure(consumer, "hygiene", policy=plain)
    typed = request(lock.PolicyRequest(tag="typed"))
    failure = refused(worktree, typed)
    assert failure.code == "missing_project"
    assert str(failure).startswith(
        "tsc runs in the repository's Node project, but the repository has no "
        "package.json."
    )
    (consumer / "package.json").write_text("{}")
    (consumer / "package-lock.json").write_text("{}")
    assert upgrade.run(worktree, typed).code == "upgraded"
    config = json.loads((consumer / ".pre-commit-config.yaml").read_bytes())
    hooks = [hook for repo in config["repos"] for hook in repo["hooks"]]
    ids = [hook.get("alias", hook["id"]) for hook in hooks]
    assert ids.index("gateseal-node-packages") < ids.index("tsc")
