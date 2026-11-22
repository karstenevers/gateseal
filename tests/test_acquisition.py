import functools
import hashlib
import http.server
import io
import os
import tarfile
import threading
from dataclasses import replace

import pytest
from gateseal.platforms import machine
from gateseal.policy import load_bundled_policy
from gateseal.tools.release_binary import Archive, mise_request
from installation import process

pytestmark = pytest.mark.integration


def install(tmp_path, version, archive, **changes):
    env = dict(
        os.environ,
        MISE_DATA_DIR=str(tmp_path / "data"),
        MISE_STATE_DIR=str(tmp_path / "state"),
        MISE_SYSTEM_DATA_DIR=str(tmp_path / "system"),
        **changes,
    )
    request = mise_request("betterleaks", version, {machine(): archive})
    return process(["mise", "--no-config", "install", request], tmp_path, env)


def installs(tmp_path):
    return list((tmp_path / "data").glob("installs/http-betterleaks-*/*"))


def test_mise_installs_a_reviewed_archive_from_the_store_with_the_network_off(
    release_archives, tmp_path
):
    assert os.environ["MISE_OFFLINE"] == "1"
    release = load_bundled_policy().tools["betterleaks"].release
    result = install(tmp_path, release.version, release.platforms[machine()])
    assert result.returncode == 0, result.stderr
    assert installs(tmp_path)


def test_mise_refuses_an_archive_outside_the_store_without_a_download(
    release_archives, tmp_path
):
    release = load_bundled_policy().tools["betterleaks"].release
    archive = release.platforms[machine()]
    unknown = replace(
        archive,
        url=archive.url.replace(release.version, "0.0.0"),
        sha256="1" * 64,
    )
    result = install(tmp_path, "0.0.0", unknown)
    assert result.returncode != 0
    assert "offline mode is enabled" in result.stderr, result.stderr
    assert not installs(tmp_path)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


@pytest.fixture
def served(tmp_path):
    folder = tmp_path / "served"
    folder.mkdir()
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as archive:
        program = tarfile.TarInfo("betterleaks")
        program.mode = 0o755
        archive.addfile(program, io.BytesIO())
    (folder / "betterleaks.tar.gz").write_bytes(data.getvalue())
    handler = functools.partial(Quiet, directory=folder)
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield (
            f"http://127.0.0.1:{server.server_port}/betterleaks.tar.gz",
            hashlib.sha256(data.getvalue()).hexdigest(),
        )
        server.shutdown()


def test_mise_checks_a_downloaded_archive_against_its_checksum(served, tmp_path):
    url, sha256 = served
    online = {
        "MISE_OFFLINE": "0",
        "MISE_CACHE_DIR": str(tmp_path / "mise-cache"),
        "NO_PROXY": "127.0.0.1",
        "no_proxy": "127.0.0.1",
    }
    wrong = Archive(url, "0" * 64, "betterleaks", None)
    result = install(tmp_path, "1.0.0", wrong, **online)
    assert result.returncode != 0
    assert "Checksum mismatch" in result.stderr, result.stderr
    assert "Expected: sha256:" + "0" * 64 in result.stderr
    assert sha256 in result.stderr
    assert not installs(tmp_path)
    exact = Archive(url, sha256, "betterleaks", None)
    result = install(tmp_path, "1.0.0", exact, **online)
    assert result.returncode == 0, result.stderr
    assert installs(tmp_path)
