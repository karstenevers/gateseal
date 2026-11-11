"""gateseal upgrade edits gateseal.toml byte for byte.

Each test compares the whole edited Manifest, because an edit that parses
right can still drop a blank line or escape a reason someone wrote.
"""

from datetime import date

from gateseal.manifest import BundledSource, ExceptionRequest
from gateseal.manifest_toml import edited

MANIFEST = """\
schema = 1
capabilities = ["hygiene", "secrets"]

[policy]
source = "git"
repository = "https://policy.example/team/gateseal-policy.git"
path = "policy"
commit = "1111111111111111111111111111111111111111"
tag = "v1"

[choices]

[exceptions]

# Fixtures trip 1.9.0.
[exceptions.betterleaks]
version = "1.8.0"
reason = "1.9.0 flags our fixtures"
review_by = 2026-12-15
"""

GIT = """\
source = "git"
repository = "https://policy.example/team/gateseal-policy.git"
path = "policy"
commit = "1111111111111111111111111111111111111111"
tag = "v1"
"""

HOOKS = ExceptionRequest(
    "pre-commit-hooks", "5.0.0", "6.0.0 rejects our YAML", date(2027, 1, 31)
)
HOOKS_TABLE = """\
[exceptions.pre-commit-hooks]
version = "5.0.0"
reason = "6.0.0 rejects our YAML"
review_by = 2027-01-31
"""


def test_a_shorter_policy_table_and_a_renewed_exception_land_in_place():
    renewed = ExceptionRequest(
        "betterleaks", "1.8.0", "Prüft unsere Fixtures falsch", date(2027, 3, 1)
    )
    result = edited(
        MANIFEST.encode(), BundledSource("2026.10"), {"betterleaks": renewed}
    )
    assert result.decode() == MANIFEST.replace(
        GIT, 'source = "bundled"\nrelease = "2026.10"\n'
    ).replace(
        'reason = "1.9.0 flags our fixtures"\nreview_by = 2026-12-15\n',
        'reason = "Prüft unsere Fixtures falsch"\nreview_by = 2027-03-01\n',
    )


def test_an_exception_after_a_last_line_without_newline_keeps_one_blank_line():
    crlf = MANIFEST.replace("\n", "\r\n").removesuffix("\r\n")
    result = edited(crlf.encode(), None, {"pre-commit-hooks": HOOKS}).decode()
    assert result == crlf + "\r\n\r\n" + HOOKS_TABLE.replace("\n", "\r\n")


def test_a_one_line_manifest_gets_its_first_exception():
    result = edited(b"[exceptions]\n", None, {"pre-commit-hooks": HOOKS}).decode()
    assert result == "[exceptions]\n\n" + HOOKS_TABLE


BARE = 'schema = 1\ncapabilities = ["hygiene"]\n'


def test_a_manifest_without_exceptions_gets_its_first_exception_at_the_end():
    result = edited(BARE.encode(), None, {"pre-commit-hooks": HOOKS}).decode()
    assert result == BARE + "\n" + HOOKS_TABLE


def test_a_policy_change_leaves_a_manifest_without_exceptions_without_them():
    bundled = BARE + '\n[policy]\nsource = "bundled"\nrelease = "2026.09"\n'
    changes = {"pre-commit-hooks": None}
    result = edited(bundled.encode(), BundledSource("2026.10"), changes).decode()
    assert result == bundled.replace("2026.09", "2026.10")
