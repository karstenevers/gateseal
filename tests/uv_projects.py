"""A uv project with one dependency, for tests of tools that run in the project.

The package imports attrs, so a type checker passes only when it reads the
packages that the project's uv.lock pins.
"""

from installation import success

PYPROJECT = """\
[project]
name = "shop"
version = "0"
requires-python = ">=3.12"
dependencies = ["attrs==26.1.0"]
"""
PRICING = """\
import attrs


@attrs.frozen
class Price:
    cents: int


def total(prices: list[Price]) -> int:
    return sum(price.cents for price in prices)
"""
CHECKOUT = """\
from shop.pricing import Price, total

print(total([Price(250), Price(100)]))
"""
WRONG_FIELD_TYPE = CHECKOUT.replace("Price(100)", 'Price("100")')
"""An error that only attrs' own types show: Price's fields come from attrs."""
TOTAL_WITH_TAX = PRICING.replace(
    "def total(prices: list[Price]) -> int:",
    "def total(prices: list[Price], tax: int) -> int:",
).replace("for price in prices)", "for price in prices) + tax")
"""A new required parameter, which the unchanged checkout.py does not pass."""


OWN_NODEENV = {
    "pyproject.toml": PYPROJECT.replace('"attrs==26.1.0"', '"attrs==26.1.0", "nodeenv"')
    + '\n[tool.uv.sources]\nnodeenv = { path = "vendor/nodeenv" }\n',
    "vendor/nodeenv/pyproject.toml": """\
[project]
name = "nodeenv"
version = "99"
requires-python = ">=3.12"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""",
    "vendor/nodeenv/src/nodeenv/__init__.py": "def marker() -> int:\n    return 1\n",
    "marker.py": """\
# pyright: reportMissingTypeStubs=false
import nodeenv

print(nodeenv.marker())
""",
}
"""The project with a package of its own named nodeenv, which marker.py calls.

Pyright installs the nodeenv of PyPI, which has no marker. The package has no
type stubs, as the nodeenv of PyPI has none, so marker.py turns that rule off.
"""


WORKSPACE_MEMBER = {
    "pyproject.toml": PYPROJECT + '\n[tool.uv.workspace]\nmembers = ["packages/*"]\n',
    "packages/labels/pyproject.toml": """\
[project]
name = "labels"
version = "0"
requires-python = ">=3.12"
dependencies = ["idna==3.11"]
""",
    "packages/labels/labels.py": 'import idna\n\nprint(idna.encode("example.com"))\n',
}
"""The project as the root of a uv workspace, whose member alone needs idna."""


def write_files(root, files, env=None):
    """Write files to root, and lock the project."""
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    success(["uv", "lock"], root, env)
    return root


def write_project(root, package="src/shop", env=None):
    """Write the project to root with its package in package, and lock it."""
    files = {
        "pyproject.toml": PYPROJECT,
        f"{package}/__init__.py": "",
        f"{package}/pricing.py": PRICING,
        "checkout.py": CHECKOUT,
    }
    return write_files(root, files, env)
