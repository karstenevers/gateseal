"""Policy fragments that the docs show are part of a policy that tests run.

The policy-format guide's alternatives fragment is part of add-a-tool's Black
policy, and the TypeScript guide's tables are part of the bundled policy. The
add-a-tool guide's policies have tests of their own, which lock, compile and
run them.
"""

import tomllib

from doc_examples import example
from gateseal.policy import bundled_root


def within(part, whole):
    """Return whether every key of the TOML table part holds its value in whole."""
    if isinstance(part, dict):
        return isinstance(whole, dict) and all(
            key in whole and within(value, whole[key]) for key, value in part.items()
        )
    return part == whole


def test_the_alternatives_example_is_part_of_the_black_policy():
    alternatives = tomllib.loads(
        example("policy-format.md", "policy tables: alternatives")
    )
    black = tomllib.loads(example("add-a-tool.md", "policy tables: black"))
    assert alternatives["capabilities"]["python"]["alternatives"]
    assert within(alternatives, black)


def test_the_typescript_example_is_part_of_the_bundled_policy():
    shown = tomllib.loads(example("typescript.md", "policy tables: typescript"))
    bundled = tomllib.loads((bundled_root() / "policy.toml").read_text())
    typescript = bundled["capabilities"]["typescript"]
    assert typescript["tools"]
    assert shown == {
        "node": bundled["node"],
        "capabilities": {"typescript": typescript},
    }


def test_the_typescript_dependency_example_is_part_of_the_bundled_policy():
    shown = tomllib.loads(
        example("typescript.md", "policy tables: typescript dependencies")
    )
    capabilities = tomllib.loads((bundled_root() / "policy.toml").read_text())[
        "capabilities"
    ]
    names = ("typescript-audit", "typescript-unused")
    assert shown == {"capabilities": {name: capabilities[name] for name in names}}
