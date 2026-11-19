"""The mypy tool that docs/add-a-tool.md publishes.

The how-to's tools/mypy.toml and policy table are the files these helpers
write into a copy of the bundled policy, so the example a team copies is the
one the tests run.
"""

import json

from doc_examples import example
from gateseal.validation import canonical

DEFINITION = example("add-a-tool.md", "policy file: tools/mypy.toml")
TABLE = example("add-a-tool.md", "policy tables: mypy")
VERSION = "2.4.1"
RELEASE = f"https://pypi.org/pypi/mypy/{VERSION}/json"
"""Where gateseal policy lock asks PyPI for the how-to's mypy release."""


def add_mypy(data, locked=True):
    """Run mypy in place of Pyright in the bundled policy copy in data.

    locked writes the fact gateseal policy lock records, so no test needs PyPI
    to load the policy.
    """
    (data / "tools").mkdir(exist_ok=True)
    (data / "tools/mypy.toml").write_text(DEFINITION)
    policy = data / "policy.toml"
    text = policy.read_text()
    start, end = (
        text.index("[capabilities.python-types."),
        text.index("[capabilities.secrets."),
    )
    policy.write_text(text[:start] + TABLE + "\n" + text[end:])
    lock = json.loads((data / "policy.lock").read_bytes())
    del lock["components"]["pyright"]
    if locked:
        lock["components"]["mypy"] = {"versions": {VERSION: {"package": "mypy"}}}
    (data / "policy.lock").write_bytes(canonical(lock))
