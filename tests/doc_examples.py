"""The TOML blocks that the docs publish, so a test runs what a reader copies."""

import re
import textwrap
from pathlib import Path

DOCS = Path(__file__).parents[1] / "docs"


def example(page, marker):
    """Return the TOML block that docs/page shows under <!-- marker -->."""
    found = re.search(
        rf"^( *)<!-- {re.escape(marker)} -->\n\1```toml\n(.*?)^\1```$",
        (DOCS / page).read_text(),
        re.MULTILINE | re.DOTALL,
    )
    assert found, f"{page} shows no {marker}"
    return textwrap.dedent(found[2])
