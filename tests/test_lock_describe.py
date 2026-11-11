"""status names the Hook Python only when a selected hook runs on it.

The team policy of test_hook_python pins a Hook Python other than the
compiler's own CPython. Here its hygiene runs Betterleaks alone, a release
binary, so a hygiene-only selection runs no hook on Python.
"""

import json

from gateseal.policy import load_policy_tree
from gateseal.validation import canonical
from test_hook_python import consumer, messages, team


def binary_hygiene(tmp_path):
    """Return the team policy with Betterleaks as hygiene's only tool."""
    root = team(tmp_path)
    toml = root / "policy.toml"
    text = (
        toml.read_text()
        .replace('tools = ["pre-commit-hooks"]', 'tools = ["betterleaks"]')
        .replace('[capabilities.secrets]\ntools = ["betterleaks"]\n', "")
    )
    kept, dropped = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith("["):
            dropped = line.startswith(
                ("[tools.pre-commit-hooks]", "[components.pre-commit-hooks")
            )
        if not dropped:
            kept.append(line)
    toml.write_text("".join(kept))
    lock = json.loads((root / "policy.lock").read_bytes())
    del lock["components"]["pre-commit-hooks"]
    (root / "policy.lock").write_bytes(canonical(lock))
    return load_policy_tree(root)


def test_a_hook_python_no_selected_hook_runs_on_goes_unnamed(tmp_path):
    policy = binary_hygiene(tmp_path)
    assert policy.hook_python is not None
    locked, _ = consumer(policy, ("hygiene",))
    assert [tool.definition.name for tool in locked.selection.tools] == ["betterleaks"]
    assert messages(locked, "hook_python") == []
