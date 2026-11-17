"""The Black alternative that docs/add-a-tool.md publishes.

The how-to's tools/black.toml, rules/black.toml and policy tables are the
files these helpers write into a copy of the bundled policy, so the example a
team copies is the one the tests run.
"""

import json

from doc_examples import example
from gateseal.validation import canonical

REVISION = "a5439e9af2ff9371297bb1a76a01b8ad17989b88"
"""The commit that the mirror's tag 26.10.1 names."""


def add_black(data, locked=True):
    """Offer Black as the python formatter in the bundled policy copy in data.

    locked writes the fact gateseal policy lock records, so no test needs
    upstream to load the policy.
    """
    for name in ("tools/black.toml", "rules/black.toml"):
        (data / name).parent.mkdir(exist_ok=True)
        (data / name).write_text(example("add-a-tool.md", f"policy file: {name}"))
    policy = data / "policy.toml"
    text = policy.read_text()
    start, end = (
        text.index("[capabilities.python."),
        text.index("[capabilities.secrets."),
    )
    tables = example("add-a-tool.md", "policy tables: black")
    policy.write_text(text[:start] + tables + "\n" + text[end:])
    lock = json.loads((data / "policy.lock").read_bytes())
    lock["components"]["black"] = {"versions": {"26.10.1": {"revision": REVISION}}}
    if locked:
        (data / "policy.lock").write_bytes(canonical(lock))
