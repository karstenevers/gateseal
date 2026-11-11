"""gateseal upgrade writes new Version Exceptions in the order it is asked for.

upgrade passes the component targets in command-line order, so a Manifest
lists new exceptions as the Repository Maintainer named them.
"""

from datetime import date

from gateseal.manifest import ExceptionRequest
from gateseal.manifest_toml import edited

MANIFEST = """\
schema = 1
capabilities = ["hygiene", "python", "secrets"]

[policy]
source = "bundled"
release = "2026.10"

[exceptions]

[exceptions.ruff]
version = "0.14.0"
reason = "0.15 reformats our generated code"
review_by = 2027-02-01
"""

HOOKS = ExceptionRequest(
    "pre-commit-hooks", "5.0.0", "6.0.0 rejects our YAML", date(2027, 1, 31)
)
BETTERLEAKS = ExceptionRequest(
    "betterleaks", "1.8.0", "1.9.0 flags our fixtures", date(2026, 12, 15)
)


def test_two_new_exceptions_follow_the_last_one_in_request_order():
    requests = {"pre-commit-hooks": HOOKS, "betterleaks": BETTERLEAKS}
    assert edited(MANIFEST.encode(), None, requests).decode() == (
        MANIFEST
        + "\n"
        + "[exceptions.pre-commit-hooks]\n"
        + 'version = "5.0.0"\n'
        + 'reason = "6.0.0 rejects our YAML"\n'
        + "review_by = 2027-01-31\n"
        + "\n"
        + "[exceptions.betterleaks]\n"
        + 'version = "1.8.0"\n'
        + 'reason = "1.9.0 flags our fixtures"\n'
        + "review_by = 2026-12-15\n"
    )
