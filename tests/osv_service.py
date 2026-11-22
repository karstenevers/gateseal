"""A local vulnerability service that answers as the OSV API does.

uv audit asks POST /v1/querybatch which records affect each locked package
version, then GET /v1/vulns/<id> for each record. The service answers from
the records a test gives it and keeps each request's path, so a test can
tell whether an audit asked at all.
"""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IDNA_ADVISORY = {
    "schema_version": "1.6.0",
    "id": "GHSA-test-idna-0001",
    "modified": "2026-01-01T00:00:00Z",
    "published": "2026-01-01T00:00:00Z",
    "aliases": ["CVE-2026-00001"],
    "summary": "Slow decoding of crafted labels in idna",
    "details": "A record that the tests write for idna 3.6.",
    "affected": [
        {
            "package": {"ecosystem": "PyPI", "name": "idna"},
            "ranges": [
                {
                    "type": "ECOSYSTEM",
                    "events": [{"introduced": "0"}, {"fixed": "3.7"}],
                }
            ],
            "versions": ["3.6"],
        }
    ],
}
"""A record for idna 3.6 with a fix in 3.7, written for the tests."""


@dataclass
class Service:
    url: str
    records: list[dict[str, object]]
    requests: list[str] = field(default_factory=list[str])


def _affects(record: dict[str, object], name: str, version: str) -> bool:
    affected = record["affected"]
    assert isinstance(affected, list)
    return any(
        entry["package"]["name"] == name and version in entry["versions"]
        for entry in affected
    )


@contextmanager
def osv_service(records=(IDNA_ADVISORY,)) -> Iterator[Service]:
    """Serve records on a free local port while the block runs."""
    service = Service("", list(records))

    class Handler(BaseHTTPRequestHandler):
        def answer(self, body: object, status: int = 200) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            service.requests.append(f"POST {self.path}")
            length = int(self.headers["Content-Length"])
            queries = json.loads(self.rfile.read(length))["queries"]
            results = []
            for query in queries:
                name, version = query["package"]["name"], query["version"]
                found = [r for r in service.records if _affects(r, name, version)]
                vulns = [{"id": r["id"], "modified": r["modified"]} for r in found]
                results.append({"vulns": vulns} if vulns else {})
            self.answer({"results": results})

        def do_GET(self) -> None:
            service.requests.append(f"GET {self.path}")
            wanted = self.path.rsplit("/", 1)[-1]
            found = [r for r in service.records if r["id"] == wanted]
            self.answer(
                found[0] if found else {"message": "Bug not found"},
                200 if found else 404,
            )

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    service.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield service
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
