"""The Prettier for Markdown that docs/add-a-tool.md publishes.

The how-to's tools/prettier-markdown.toml, rules/prettier-markdown.toml and
policy tables are the files these helpers write into a copy of the bundled
policy, so the example a team copies is the one the tests run.
"""

from doc_examples import example

NODE = example("add-a-tool.md", "policy tables: node")
TABLES = example("add-a-tool.md", "policy tables: prettier-markdown")


def add_prettier_markdown(data):
    """Add the how-to's docs capability to the bundled policy copy in data.

    The copy pins the how-to's Node already. gateseal policy lock writes the
    Policy Lock.
    """
    for name in ("tools/prettier-markdown.toml", "rules/prettier-markdown.toml"):
        (data / name).parent.mkdir(exist_ok=True)
        (data / name).write_text(example("add-a-tool.md", f"policy file: {name}"))
    policy = data / "policy.toml"
    text = policy.read_text()
    assert f"\n{NODE}" in text
    policy.write_text(f"{text}\n{TABLES}")
