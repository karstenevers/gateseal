"""The archives gateseal policy lock downloads for a release binary it locks anew.

Each test serves Betterleaks 1.9.0's release files from memory, as upstream
would, with its Linux archive broken, and locks the version without a record.
"""

import hashlib

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.tools import built_in_tools

RELEASE = "https://github.com/betterleaks/betterleaks/releases/download/v1.9.0/"
LINUX = RELEASE + "betterleaks_1.9.0_linux_x64.tar.gz"
MACOS = RELEASE + "betterleaks_1.9.0_darwin_arm64.tar.gz"


class Releases:
    def __init__(self, files):
        self.files = files

    def tagged(self, repository, tag):
        return None

    def download(self, url):
        return self.files.get(url)


def published():
    archives = {LINUX: b"linux archive", MACOS: b"macos archive"}
    listing = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {url.rsplit('/', 1)[1]}\n"
        for url, data in archives.items()
    )
    return {RELEASE + "checksums.txt": listing.encode()} | archives


@pytest.mark.parametrize(
    "files,code",
    [
        (published() | {LINUX: b"replaced"}, "upstream_checksum_mismatch"),
        (
            {url: data for url, data in published().items() if url != LINUX},
            "upstream_asset_missing",
        ),
    ],
    ids=["mismatch", "missing"],
)
def test_an_archive_locked_anew_must_have_its_listed_sha256(files, code):
    tool = built_in_tools()["betterleaks"]
    with pytest.raises(GatesealError) as failure:
        tool.source.lock(tool, "1.9.0", None, Releases(files))
    assert failure.value.code == code
    assert LINUX in str(failure.value)
