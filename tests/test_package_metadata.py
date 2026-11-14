"""The wheel's metadata, which PyPI shows on the project page."""

import re
import tomllib
import zipfile
from email.parser import Parser

import pytest
from installation import PACKAGE

BLOB = "https://github.com/karstenevers/gateseal/blob/main/"
RAW = "https://raw.githubusercontent.com/karstenevers/gateseal/main/"
TARGET = r'(?:(?:href|src|srcset)="|\]\(|^ {0,3}\[(?!\^)[^\]]+\]:[ \t]*<?)'
RELATIVE = re.compile(TARGET + r"(?![a-zA-Z][a-zA-Z0-9+.-]*:|#|//|[<\s])", re.M)
PYPROJECT = tomllib.loads((PACKAGE / "pyproject.toml").read_text())
SUBSTITUTIONS = PYPROJECT["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"][
    "substitutions"
]


def described(readme):
    for substitution in SUBSTITUTIONS:
        readme = re.sub(substitution["pattern"], substitution["replacement"], readme)
    return readme


@pytest.mark.parametrize(
    ("readme", "description"),
    [
        ("[Install](docs/install.md)", f"[Install]({BLOB}docs/install.md)"),
        ("[Install](/docs/install.md)", f"[Install]({BLOB}docs/install.md)"),
        ('<a href="docs/demo.md">', f'<a href="{BLOB}docs/demo.md">'),
        ('<img src="docs/a.svg">', f'<img src="{RAW}docs/a.svg">'),
        ('<source srcset="/docs/a.svg">', f'<source srcset="{RAW}docs/a.svg">'),
        ("![Logo](docs/a.png)", f"![Logo]({RAW}docs/a.png)"),
        ("[docs]: docs/install.md", f"[docs]: {BLOB}docs/install.md"),
        ("  [docs]: <docs/install.md>", f"  [docs]: <{BLOB}docs/install.md>"),
        ("[Install](#install)", "[Install](#install)"),
        ("[Mail](mailto:team@example.com)", "[Mail](mailto:team@example.com)"),
        ("[PyPI](https://pypi.org)", "[PyPI](https://pypi.org)"),
        ('<img src="//example.com/a.svg">', '<img src="//example.com/a.svg">'),
        ("[docs]: https://example.com", "[docs]: https://example.com"),
        ("[docs]: <https://example.com>", "[docs]: <https://example.com>"),
        ("[^1]: docs/install.md", "[^1]: docs/install.md"),
    ],
)
def test_description_points_relative_links_into_the_repository(readme, description):
    assert described(readme) == description


def test_project_page_shows_the_readme_with_links_into_the_repository(installed):
    with zipfile.ZipFile(installed.wheel) as wheel:
        (name,) = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
        metadata = Parser().parsestr(wheel.read(name).decode())
    description = metadata.get_payload()

    assert metadata["Description-Content-Type"] == "text/markdown"
    assert description == described((PACKAGE / "README.md").read_text())
    assert RELATIVE.findall(description) == []
    assert f'src="{RAW}docs/assets/header-light.svg"' in description
    assert f"]({BLOB}docs/install.md)" in description
