"""The Black alternative and Hook Python that docs/add-a-tool.md publishes.

The how-to's tools/black.toml, rules/black.toml and policy tables are the
files these helpers write into a copy of the bundled policy, so the example a
team copies is the one the tests run.
"""

import json
import re
import textwrap
from pathlib import Path

from gateseal.compiler_release import interpreter_records
from gateseal.validation import canonical

DOC = Path(__file__).parents[1] / "docs" / "add-a-tool.md"
BLACK = "https://github.com/psf/black-pre-commit-mirror"
REVISION = "a5439e9af2ff9371297bb1a76a01b8ad17989b88"
"""The commit that the mirror's tag 26.10.1 names."""

BUILD = "20261026"
SUMS = f"""\
{"0" * 64}  cpython-3.15.2+{BUILD}-x86_64-unknown-linux-gnu-full.tar.zst
1b1b03fca8aa351698403f3d100ee83db7a5d99748fc7707681ac31a6a846ad9  \
cpython-3.15.2+{BUILD}-aarch64-apple-darwin-install_only_stripped.tar.gz
7b03163a980c607ba8194709d4a4086735a7a9c488cc475b098157306927b36b  \
cpython-3.15.2+{BUILD}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz
"""
"""The lines of the build's SHA256SUMS that name CPython 3.15.2's archives."""

PYTHON = interpreter_records(SUMS, "3.15.2", BUILD)
"""The Policy Lock record of CPython 3.15.2, as gateseal policy add writes it."""

HOOK_PYTHON = (
    '[components.python]\npin = "3.15.2"\n\n[components.python.versions."3.15.2"]\n'
)

LAZY = "lazy import json\n\nconfig=json.loads('{}')\n"
"""A file that only CPython 3.15 parses, with a line that Black reformats.

Its imports are sorted, so the bundled Ruff rules leave the file as it is.
"""

FORMATTED = 'lazy import json\n\nconfig = json.loads("{}")\n'
"""LAZY as Black formats it."""


def example(marker):
    """Return the TOML block that the how-to shows under <!-- marker -->."""
    found = re.search(
        rf"^( *)<!-- {re.escape(marker)} -->\n\1```toml\n(.*?)^\1```$",
        DOC.read_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert found, f"{DOC.name} shows no {marker}"
    return textwrap.dedent(found[2])


def add_black(data, python=True, locked=True):
    """Offer Black as the python formatter in the bundled policy copy in data.

    python pins the Hook Python. locked writes the facts gateseal policy lock
    and add record, so no test needs upstream to load the policy.
    """
    for name in ("tools/black.toml", "rules/black.toml"):
        (data / name).parent.mkdir(exist_ok=True)
        (data / name).write_text(example(f"policy file: {name}"))
    policy = data / "policy.toml"
    text = policy.read_text()
    replaced = '[capabilities.python]\ntools = ["ruff"]\n'
    assert replaced in text
    text = text.replace(replaced, "") + "\n" + example("policy tables: black")
    policy.write_text(text + (f"\n{HOOK_PYTHON}" if python else ""))
    lock = json.loads((data / "policy.lock").read_bytes())
    lock["components"]["black"] = {"versions": {"26.10.1": {"revision": REVISION}}}
    if python:
        lock["components"]["python"] = {"versions": {"3.15.2": PYTHON}}
    if locked:
        (data / "policy.lock").write_bytes(canonical(lock))
