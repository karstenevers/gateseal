"""The audit of an npm project's package-lock.json, which runs only in the gate.

OSV-Scanner asks the OSV service at a fixed URL, so these tests answer from a
recorded database instead: a team release whose audit tool is the bundled
definition with only --offline-vulnerabilities added. OSV-Scanner then reads
the advisories from all.zip in the user's cache folder, which mise keeps
through HOME. One network test asks the real service with the bundled
definition.
"""

import json
import threading
import zipfile
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path

import pytest
from gateseal.validation import canonical
from installation import clone, process, success
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

ADVISORY = "GHSA-test-mini-0001"
RECORD = {
    "schema_version": "1.6.0",
    "id": ADVISORY,
    "modified": "2026-01-01T00:00:00Z",
    "published": "2026-01-01T00:00:00Z",
    "summary": "Prototype pollution in minimist",
    "details": "A record that the tests write for minimist 1.2.5.",
    "affected": [
        {
            "package": {"ecosystem": "npm", "name": "minimist"},
            "ranges": [
                {"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.2.6"}]}
            ],
        }
    ],
}
"""A record for minimist below 1.2.6, written for the tests."""
CACHES = (".cache", "Library/Caches")
"""The user cache folder below HOME on Linux and on macOS."""
DEFINITION = (
    files("gateseal").joinpath("data", "tools", "osv-scanner.tool.toml").read_text()
)
ONLINE = '"scan", "source",'
PACKAGE = """\
{
  "name": "shop",
  "version": "1.0.0",
  "private": true,
  "dependencies": {"minimist": "%s"}
}
"""
LOCKFILE = """\
{
  "name": "shop",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "shop",
      "version": "1.0.0",
      "dependencies": {
        "minimist": "%(version)s"
      }
    },
    "node_modules/minimist": {
      "version": "%(version)s",
      "resolved": "https://registry.npmjs.org/minimist/-/minimist-%(version)s.tgz",
      "integrity": "%(integrity)s",
      "license": "MIT"
    }
  }
}
"""
"""The lockfile that npm 11.19.1 writes for package.json with minimist alone."""
INTEGRITY = {
    "1.2.5": "sha512-FM9nNUYrRBAELZQT3xeZQ7fmMOBg6nWNmJKTcgsJeaLstP/UODVpGsr5OhXhhXg6f+qtJ8uiZ+PUxkDWcgIXLw==",  # noqa: E501
    "1.2.8": "sha512-2yyAR8qBkN3YuheJanUpWC5U3bb5osDywNB8RzDVlDwDHbocAJveqqj1u8+SVD7jkWT4yvsHCpWqqWqAxb0zCA==",  # noqa: E501
}


def project(version="1.2.8", extra=None):
    """Return the files of an npm project that depends on minimist at version."""
    lockfile = LOCKFILE % {"version": version, "integrity": INTEGRITY[version]}
    return {
        "package.json": PACKAGE % version,
        "package-lock.json": lockfile,
        **(extra or {}),
    }


def write(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


def recorded_audit(ignored=()):
    """Return the change that runs the bundled audit on the recorded database.

    Built-in names are reserved, so the team's definition is local-audit.
    ignored fills the policy's list of ignored advisories.
    """

    def change(data):
        assert DEFINITION.count(ONLINE) == 1
        (data / "tools").mkdir(exist_ok=True)
        (data / "tools/local-audit.toml").write_text(
            DEFINITION.replace(ONLINE, ONLINE + ' "--offline-vulnerabilities",')
        )
        policy = data / "policy.toml"
        policy.write_text(
            policy.read_text().replace(
                "[capabilities.typescript-audit.tools.osv-scanner]",
                "[capabilities.typescript-audit.tools.local-audit]",
            )
        )
        lock = json.loads((data / "policy.lock").read_bytes())
        lock["components"]["local-audit"] = lock["components"].pop("osv-scanner")
        (data / "policy.lock").write_bytes(canonical(lock))
        with (data / "rules/osv-scanner.toml").open("a") as rules:
            for advisory in ignored:
                rules.write(
                    f'\n[[IgnoredVulns]]\nid = "{advisory}"\n'
                    'reason = "The tests ignore it."\n'
                )

    return change


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    """The team policy: v1 audits on the recorded database, v2 ignores ADVISORY."""
    origin = tmp_path_factory.mktemp("audit policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", recorded_audit())
    publish(origin, "v2", recorded_audit([ADVISORY]))
    return origin


@pytest.fixture
def recorded(installed):
    """Write the recorded database of npm advisories while the test runs."""
    home = Path(installed.environment["HOME"])
    databases = [home / cache / "osv-scalibr/npm/all.zip" for cache in CACHES]
    for path in databases:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w") as database:
            database.writestr(f"{ADVISORY}.json", json.dumps(RECORD))
    yield
    for path in databases:
        path.unlink()


def adopt(installed, root, files, env=None, source=()):
    """Write files in root, configure typescript-audit and commit."""
    write(root, files)
    flags = ("--capability", "hygiene", "--capability", "typescript-audit")
    installed.cli(root, "configure", *flags, *source, env=env)
    success(["git", "add", "-A"], root)
    success(["git", "commit", "-m", "Adopt the audit"], root, env)
    return root


def audited(installed, origin, root, tag, files):
    """Return a consumer of the team release tag with files, and its environment."""
    env = environment(installed, origin)
    consumer = clone(root / "origin", root / "consumer")
    source = ("--policy", URL, "--policy-path", "policy", "--policy-tag", tag)
    return adopt(installed, consumer, files, env, source), env


def hook_statuses(result):
    entries = [d for d in result["diagnostics"] if d["category"] == "hook"]
    return {entry["hook"]: entry["status"] for entry in entries}


def details(result):
    """Return the output of pre-commit that a result carries."""
    return result["diagnostics"][1]["message"]


def gate(installed, consumer, env, code):
    return installed.cli(consumer, "gate", code=code, env=env)


def test_the_audit_runs_only_in_the_gate(installed, origin, recorded, tmp_path):
    """A vulnerable lockfile passes a commit, a push and gateseal run, then fails
    the gate.

    A repository hook at a push gives the clone a push launcher, so pre-commit
    runs at the push.
    """
    consumer, env = audited(installed, origin, tmp_path, "v1", project("1.2.5"))
    write(consumer, {"scripts/ready.sh": "#!/bin/sh\necho ready\n"})
    (consumer / "scripts/ready.sh").chmod(0o755)
    manifest = consumer / "gateseal.toml"
    manifest.write_text(
        manifest.read_text() + '\n[hooks.ready]\nname = "ready"\n'
        'run = "scripts/ready.sh"\nstages = ["push", "ci"]\n'
    )
    installed.cli(consumer, "configure", env=env)
    installed.cli(consumer, "setup", "--refresh", env=env)
    success(["git", "add", "-A"], consumer)
    committed = process(["git", "commit", "-m", "Change"], consumer, env)
    shown = committed.stdout + committed.stderr
    assert committed.returncode == 0, shown
    assert "osv-scanner" not in shown
    pushed = process(["git", "push", "origin", "HEAD:refs/heads/topic"], consumer, env)
    shown = pushed.stdout + pushed.stderr
    assert pushed.returncode == 0, shown
    assert "ready" in shown and "osv-scanner" not in shown
    ran = installed.cli(consumer, "run", env=env)
    assert "local-audit" not in hook_statuses(ran)
    assert "osv-scanner" not in details(ran)
    gated = gate(installed, consumer, env, 1)
    assert gated["code"] == "checks_failed"
    assert hook_statuses(gated)["osv-scanner"] == "failed"


def test_the_gate_names_the_package_the_advisory_and_the_fix(
    installed, origin, recorded, tmp_path
):
    consumer, env = audited(installed, origin, tmp_path, "v1", project("1.2.5"))
    gated = gate(installed, consumer, env, 1)
    assert gated["code"] == "checks_failed"
    shown = details(gated)
    [row] = [line for line in shown.splitlines() if ADVISORY in line]
    cells = [cell.strip() for cell in row.strip("|").split("|")]
    assert cells[0] == f"https://osv.dev/{ADVISORY}"
    assert cells[2:6] == ["npm", "minimist", "1.2.5", "1.2.6"]


def test_a_clean_project_passes_the_gate(installed, origin, recorded, tmp_path):
    consumer, env = audited(installed, origin, tmp_path, "v1", project())
    gated = gate(installed, consumer, env, 0)
    assert hook_statuses(gated)["osv-scanner"] == "passed"


def test_a_lockfile_that_npm_shrinkwrap_json_hides_fails_the_gate(
    installed, origin, recorded, tmp_path
):
    """OSV-Scanner skips package-lock.json beside npm-shrinkwrap.json and finds
    no package, so the audit stops the gate and never passes unread."""
    files = project("1.2.5")
    files["npm-shrinkwrap.json"] = files["package-lock.json"]
    consumer, env = audited(installed, origin, tmp_path, "v1", files)
    gated = gate(installed, consumer, env, 4)
    assert gated["code"] == "tool_failed"
    assert hook_statuses(gated)["osv-scanner"] != "passed"


def test_an_audit_without_its_advisories_fails_the_gate_as_a_tool_failure(
    installed, origin, tmp_path
):
    """Without the recorded database, OSV-Scanner cannot read any advisory, as
    when the bundled definition cannot reach the service."""
    consumer, env = audited(installed, origin, tmp_path, "v1", project())
    gated = gate(installed, consumer, env, 4)
    assert gated["code"] == "tool_failed"
    assert (
        "The osv-scanner hook stopped with exit code 127."
        in gated["diagnostics"][0]["message"]
    )
    assert hook_statuses(gated)["osv-scanner"] == "stopped"


def test_an_advisory_that_the_policy_ignores_passes(
    installed, origin, recorded, tmp_path
):
    consumer, env = audited(installed, origin, tmp_path, "v2", project("1.2.5"))
    rules = (consumer / ".gateseal/local-audit.toml").read_text()
    assert f'id = "{ADVISORY}"' in rules
    gated = gate(installed, consumer, env, 0)
    assert hook_statuses(gated)["osv-scanner"] == "passed"


REPOSITORY_IGNORES = {
    "advisory": f'[[IgnoredVulns]]\nid = "{ADVISORY}"\nreason = "r"\n',
    "package": (
        '[[PackageOverrides]]\nname = "minimist"\necosystem = "npm"\n'
        'ignore = true\nreason = "r"\n'
    ),
}
"""An osv-scanner.toml that OSV-Scanner reads beside the lockfile without --config."""


@pytest.mark.parametrize("ignores", REPOSITORY_IGNORES.values(), ids=REPOSITORY_IGNORES)
def test_a_repository_file_cannot_ignore_an_advisory(
    installed, origin, recorded, tmp_path, ignores
):
    files = project("1.2.5", {"osv-scanner.toml": ignores})
    consumer, env = audited(installed, origin, tmp_path, "v1", files)
    gated = gate(installed, consumer, env, 1)
    assert hook_statuses(gated)["osv-scanner"] == "failed"
    assert ADVISORY in details(gated)


@contextmanager
def refusing_proxy():
    """Serve a proxy that refuses every tunnel and keeps the host it was asked for."""
    hosts = []

    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self):
            hosts.append(self.path)
            self.send_error(403)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", hosts
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "https_proxy"])
def test_the_bundled_audit_asks_the_service_through_the_proxy(
    installed, tmp_path, name
):
    """The commit builds the hook environments, so only the audit asks the proxy."""
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    adopt(installed, consumer, project())
    unset = ("HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy")
    env = {k: v for k, v in installed.environment.items() if k not in unset}
    with refusing_proxy() as (url, hosts):
        gated = gate(installed, consumer, env | {name: url}, 4)
    assert hook_statuses(gated)["osv-scanner"] == "stopped"
    assert "api.osv.dev:443" in hosts


@pytest.mark.network
def test_the_bundled_audit_asks_the_real_service(installed, tmp_path):
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    adopt(installed, consumer, project("1.2.5"))
    gated = gate(installed, consumer, None, 1)
    assert gated["code"] == "checks_failed"
    assert "GHSA-xvch-5gv4-984h" in details(gated)
