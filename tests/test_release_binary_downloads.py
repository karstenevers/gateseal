"""The archives gateseal policy lock downloads for a release binary it locks.

Each test serves Betterleaks 1.9.0's release files from memory, as upstream
would, and locks the version with or without a record. Betterleaks is
checked at commit, so its record holds the SHA-256 of each archive's program.
"""

import hashlib
from dataclasses import replace

import pytest
from archives import tar_gz
from gateseal.diagnostics import GatesealError
from gateseal.tools import built_in_tools

RELEASE = "https://github.com/betterleaks/betterleaks/releases/download/v1.9.0/"
LINUX = RELEASE + "betterleaks_1.9.0_linux_x64.tar.gz"
MACOS = RELEASE + "betterleaks_1.9.0_darwin_arm64.tar.gz"


class Releases:
    def __init__(self, files):
        self.files = files
        self.downloaded = []

    def tagged(self, repository, tag):
        return None

    def download(self, url):
        self.downloaded.append(url)
        return self.files.get(url)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def published(**replaced):
    """Return the release files, with archives replaced by platform name.

    Each archive's program is the bytes of "<platform> program".
    """
    archives = {
        url: replaced.get(
            platform,
            tar_gz(
                ("README.md", b"Betterleaks\n"),
                ("betterleaks", f"{platform} program".encode()),
            ),
        )
        for platform, url in (("linux", LINUX), ("macos", MACOS))
    }
    listing = "".join(
        f"{sha(data)}  {url.rsplit('/', 1)[1]}\n" for url, data in archives.items()
    )
    return {RELEASE + "checksums.txt": listing.encode()} | archives


def lock(files, recorded=None, checked=True):
    tool = built_in_tools()["betterleaks"]
    if not checked:
        executable = replace(tool.executable, check_at_commit=False)
        tool = replace(tool, executable=executable)
    upstream = Releases(files)
    return tool.source.lock(tool, "1.9.0", recorded, upstream), upstream.downloaded


def refused(files):
    with pytest.raises(GatesealError) as failure:
        lock(files)
    assert LINUX in str(failure.value)
    return failure.value.code


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
    assert refused(files) == code


def test_a_checked_executable_records_its_program_in_each_archive():
    record, downloaded = lock(published())
    assert {
        platform: archive["executable_sha256"]
        for platform, archive in record["platforms"].items()
    } == {"linux-x64": sha(b"linux program"), "macos-arm64": sha(b"macos program")}
    assert sorted(downloaded) == sorted([RELEASE + "checksums.txt", LINUX, MACOS])


@pytest.mark.parametrize(
    "members",
    [
        [("README.md", b"Betterleaks\n")],
        [("bin/betterleaks", b"linux program")],
        [("betterleaks", b"linux program"), ("./betterleaks/", b"")],
    ],
    ids=["no program", "program in a folder", "program replaced by a folder"],
)
def test_an_archive_without_its_program_at_the_top_is_refused(members):
    archive = tar_gz(*members)
    assert refused(published(linux=archive)) == "upstream_executable_missing"


def test_an_archive_that_is_no_tar_gz_is_refused():
    assert refused(published(linux=b"zip bytes")) == "upstream_executable_missing"


def test_the_last_member_of_the_program_name_is_the_program():
    archive = tar_gz(("betterleaks", b"first"), ("./betterleaks", b"last"))
    record, _ = lock(published(linux=archive))
    assert record["platforms"]["linux-x64"]["executable_sha256"] == sha(b"last")


def test_a_recorded_program_is_kept_without_a_download():
    files = published()
    record, _ = lock(files)
    archives = {url: b"gone" for url in (LINUX, MACOS)}
    kept, downloaded = lock(files | archives, record)
    assert (kept, downloaded) == (record, [RELEASE + "checksums.txt"])


def test_a_record_without_the_program_downloads_only_to_read_it():
    files = published()
    record, _ = lock(files)
    unchecked, downloaded = lock(files, record, checked=False)
    assert downloaded == [RELEASE + "checksums.txt"]
    assert all("executable_sha256" not in a for a in unchecked["platforms"].values())
    completed, downloaded = lock(files, unchecked)
    assert (completed, sorted(downloaded)) == (
        record,
        sorted([RELEASE + "checksums.txt", LINUX, MACOS]),
    )


@pytest.mark.parametrize("program", ["edited", 5], ids=["not a SHA-256", "no text"])
def test_a_recorded_program_that_is_no_sha256_is_read_again(program):
    files = published()
    record, _ = lock(files)
    edited = {
        "platforms": {
            platform: archive | {"executable_sha256": program}
            for platform, archive in record["platforms"].items()
        }
    }
    assert lock(files, edited)[0] == record
