"""The Python releases that gateseal trusts with HTTPS."""

import re

FIRST_TRUSTED = {(3, 11): 18, (3, 12): 16, (3, 13): 18, (3, 14): 10, (3, 15): 2}
"""The patch number of each line's first release that blocks the compromised
eMudhra intermediate certificates (CVE-2026-115557). A later line blocks them
from its first release."""


def first_trusted(release: str) -> str:
    """Return the first trusted release of release's line, such as 3.14.10 for 3.14."""
    major, minor = (int(part) for part in release.split(".")[:2])
    return f"{major}.{minor}.{FIRST_TRUSTED.get((major, minor), 0)}"


def trusted(release: str) -> bool:
    """Return whether release, such as 3.14.10, blocks the compromised certificates.

    A prerelease, such as 3.15.2rc1, comes before its release. A build after a
    release, such as 3.14.10+, counts as that release.
    """
    found = re.fullmatch(r"([0-9]+)\.([0-9]+)\.([0-9]+)(.*)", release)
    first = (*map(int, first_trusted(release).split(".")), True)
    return (
        found is not None
        and (*map(int, found.groups()[:3]), not found[4].rstrip("+")) >= first
    )
