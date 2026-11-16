"""The Black alternative that docs/add-a-tool.md publishes.

The how-to's tools/black.toml, rules/black.toml and policy tables are the
files these helpers write into a copy of the bundled policy, so the example a
team copies is the one the tests run.
"""

import json
import re
import textwrap
from pathlib import Path

from gateseal.validation import canonical

DOC = Path(__file__).parents[1] / "docs" / "add-a-tool.md"
REVISION = "a5439e9af2ff9371297bb1a76a01b8ad17989b88"
"""The commit that the mirror's tag 26.10.1 names."""


def example(marker):
    """Return the TOML block that the how-to shows under <!-- marker -->."""
    found = re.search(
        rf"^( *)<!-- {re.escape(marker)} -->\n\1```toml\n(.*?)^\1```$",
        DOC.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert found, f"{DOC.name} shows no {marker}"
    return textwrap.dedent(found[2])


def add_black(data, locked=True):
    """Offer Black as the python formatter in the bundled policy copy in data.

    locked writes the fact gateseal policy lock records, so no test needs
    upstream to load the policy.
    """
    for name in ("tools/black.toml", "rules/black.toml"):
        (data / name).parent.mkdir(exist_ok=True)
        (data / name).write_text(example(f"policy file: {name}"))
    policy = data / "policy.toml"
    text = policy.read_text()
    replaced = '[capabilities.python]\ntools = ["ruff"]\n'
    assert replaced in text
    policy.write_text(
        text.replace(replaced, "") + "\n" + example("policy tables: black")
    )
    lock = json.loads((data / "policy.lock").read_bytes())
    lock["components"]["black"] = {"versions": {"26.10.1": {"revision": REVISION}}}
    if locked:
        (data / "policy.lock").write_bytes(canonical(lock))
