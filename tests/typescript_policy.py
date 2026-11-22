"""TypeScript sources and team variants of the bundled typescript capability.

The variants are copies of the bundled policy that team_policy.publish
commits: one pins another Node, one widens Prettier's types to JSON and YAML.
"""

import json
import os
import re

from gateseal.policy import bundled_root
from gateseal.validation import canonical

NODE = "24.22.3"
"""The Node that the bundled policy pins."""

OTHER_NODE = "24.22.2"
"""The Node that a team policy pins in place of the bundled one."""

BAD = (
    "const  greeting : string = 'hello'\n"
    "export const shout = (name: string) => { return greeting + ', ' + name }\n"
)
"""A lint-clean module that Prettier formats."""

FORMATTED = (
    'const greeting: string = "hello";\n'
    "export const shout = (name: string) => {\n"
    '  return greeting + ", " + name;\n'
    "};\n"
)
"""BAD as Prettier formats it with its default options."""

DEFECT = "var count: any = 1;\nexport { count };\n"
"""A module that typescript-eslint's recommended rules refuse."""

CLEAN = "export const answer = 42;\n"

WRONG_NODE = "#!/bin/sh\necho 'the PATH Node ran' >&2\nexit 97\n"
"""A node and npm that a hook must never start."""

PRETTIER_WIDE = (
    (bundled_root().parent / "tools" / "prettier.tool.toml")
    .read_text()
    .replace('types_or = ["ts", "tsx"]', 'types_or = ["ts", "tsx", "json", "yaml"]')
)
"""Built-in Prettier as a team-defined tool that also formats JSON and YAML."""


def wrong_path(directory):
    """Return a PATH whose first folder holds a node and an npm that fail."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("node", "npm"):
        (directory / name).write_text(WRONG_NODE)
        (directory / name).chmod(0o755)
    return f"{directory}{os.pathsep}{os.environ['PATH']}"


def pin_other_node(data, lock):
    """Pin OTHER_NODE in the policy copy in data, and lock it with lock(data)."""
    policy = data / "policy.toml"
    text, count = re.subn(
        rf'^node = "{NODE}"$', f'node = "{OTHER_NODE}"', policy.read_text(), flags=re.M
    )
    assert count == 1
    policy.write_text(text)
    lock(data)


def widen_prettier(data):
    """Run Prettier through a team definition whose hook takes JSON and YAML too."""
    (data / "tools").mkdir(exist_ok=True)
    (data / "tools/prettier-wide.toml").write_text(PRETTIER_WIDE)
    policy = data / "policy.toml"
    policy.write_text(
        policy.read_text().replace(
            "[capabilities.typescript.tools.prettier]",
            "[capabilities.typescript.tools.prettier-wide]",
        )
    )
    lock = json.loads((data / "policy.lock").read_bytes())
    lock["components"]["prettier-wide"] = lock["components"].pop("prettier")
    (data / "policy.lock").write_bytes(canonical(lock))
