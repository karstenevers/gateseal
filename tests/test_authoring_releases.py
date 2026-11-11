"""Download release files for policy lock and add from a local HTTP server."""

import http.server
import socket
import threading

import pytest
from gateseal import authoring
from gateseal.diagnostics import GatesealError

RERUN = "gateseal policy add betterleaks 1.10.0"


class Releases(http.server.BaseHTTPRequestHandler):
    """Serve checksums.txt, a 404 for a missing file, and a 500 otherwise."""

    def do_GET(self):
        if self.path == "/checksums.txt":
            body = b"0" * 64 + b"  betterleaks.tar.gz\n"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404 if self.path == "/missing.txt" else 500)

    def log_message(self, format, *arguments):
        pass


@pytest.fixture(scope="module")
def server():
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Releases) as running:
        threading.Thread(
            target=running.serve_forever, args=(0.01,), daemon=True
        ).start()
        yield f"http://127.0.0.1:{running.server_address[1]}"
        running.shutdown()


@pytest.fixture
def releases(tmp_path, monkeypatch):
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.setenv(name, "*")
    return authoring.Releases("git", tmp_path, RERUN)


def failed(releases, url):
    with pytest.raises(GatesealError) as failure:
        releases.download(url)
    error = failure.value
    assert (error.code, error.exit_code) == ("upstream_unavailable", 4)
    assert all(value in error.next_step for value in (url, RERUN))
    return str(error)


def test_a_release_file_is_its_bytes_and_a_missing_one_is_none(server, releases):
    assert releases.download(f"{server}/checksums.txt") == (
        b"0" * 64 + b"  betterleaks.tar.gz\n"
    )
    assert releases.download(f"{server}/missing.txt") is None


def test_a_server_error_names_its_status(server, releases):
    url = f"{server}/broken.txt"
    message = failed(releases, url)
    assert url in message and "HTTP 500" in message


def test_an_unreachable_server_names_why(releases):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{probe.getsockname()[1]}/checksums.txt"
    message = failed(releases, url)
    assert url in message
    assert "refused" in message
