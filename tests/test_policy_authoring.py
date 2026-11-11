"""Start a team policy, review and pin versions, lock and check it.

Every test drives the installed command. Offline, the hook repositories are
local Git repositories that their GitHub URLs reach through
url.<base>.insteadOf, and a urllib opener that the driver installs serves
Betterleaks' release files from a directory, so gateseal policy lock and add
run their own code against fixtures. The network tests resolve versions from
the real upstream.
"""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import with_git_config
from gateseal.policy import bundled_root
from installation import clone, process, success

HOOKS = "https://github.com/pre-commit/pre-commit-hooks"
RUFF = "https://github.com/astral-sh/ruff-pre-commit"
RELEASES = "github.com/betterleaks/betterleaks/releases/download"
TEAM = "https://policy.example/team/gateseal-policy.git"
IDENTITY = ["-c", "user.name=Policy author", "-c", "user.email=policy@example.invalid"]
STEP = "Run gateseal policy lock in the policy directory."

OFFLINE = """
import email.message, io, os, sys, urllib.error, urllib.request, urllib.response
from pathlib import Path
from gateseal.cli import main

root = Path(sys.argv[1])
edited = os.environ.get("EDIT_DURING_DOWNLOAD")
unreachable = os.environ.get("UNREACHABLE")


class Fixtures(urllib.request.BaseHandler):
    handler_order = 100

    def https_open(self, request):
        url = request.full_url
        if edited:
            with open(edited, "a") as policy:
                policy.write("# Edited during a download.\\n")
        if unreachable or not url.startswith("https://github.com/"):
            raise urllib.error.URLError("offline")
        path = root / url.removeprefix("https://")
        headers = email.message.Message()
        if not path.is_file():
            raise urllib.error.HTTPError(url, 404, "Not Found", headers, None)
        response = urllib.response.addinfourl(
            io.BytesIO(path.read_bytes()), headers, url, 200
        )
        response.msg = "OK"
        return response


urllib.request.install_opener(urllib.request.build_opener(Fixtures))
raise SystemExit(main(sys.argv[2:]))
"""


def git(root, *arguments):
    return success(["git", *IDENTITY, *arguments], root).stdout.strip()


def release(root, version, platforms=("linux_x64", "darwin_arm64")):
    """Publish fake Betterleaks archives of version and their checksums.txt."""
    directory = root / RELEASES / f"v{version}"
    directory.mkdir(parents=True, exist_ok=True)
    lines = [f"{'0' * 64}  betterleaks_{version}_windows_x64.zip"]
    for platform in platforms:
        name = f"betterleaks_{version}_{platform}.tar.gz"
        (directory / name).write_bytes(name.encode())
        lines.append(f"{hashlib.sha256(name.encode()).hexdigest()}  {name}")
    (directory / "checksums.txt").write_text("\n".join(lines) + "\n")
    return directory


@dataclass
class Upstream:
    root: Path
    environment: dict[str, str]

    def repository(self, url):
        return self.root / url.rsplit("/", 1)[1]

    def commit(self, url, tag):
        return git(self.repository(url), "rev-parse", f"{tag}^{{commit}}")


@pytest.fixture
def upstream(installed, tmp_path):
    """Offline upstream with the bundled policy's tags and Betterleaks releases.

    v5.0.0 and v0.17.1 are annotated tags, which policy lock peels; v6.0.0 is
    lightweight.
    """
    root = tmp_path / "upstream"
    settings = {}
    for url, tags in ((HOOKS, ("v5.0.0", "v6.0.0")), (RUFF, ("v0.17.1",))):
        repository = root / url.rsplit("/", 1)[1]
        repository.mkdir(parents=True)
        git(repository, "init", "-q", "--initial-branch=main")
        for tag in tags:
            git(repository, "commit", "-q", "--allow-empty", "-m", tag)
            git(repository, "tag", *(["-a", "-m", tag] if tag != "v6.0.0" else []), tag)
        settings[f"url.{repository.as_uri()}.insteadOf"] = url
    for version in ("1.8.0", "1.9.0"):
        release(root, version)
    environment = {
        key: value
        for key, value in installed.environment.items()
        if not key.startswith("GIT_CONFIG_")
    }
    return Upstream(root, with_git_config(environment, settings))


def policy_command(installed, upstream, directory, *action, code=0, env=None):
    """Run gateseal policy <action> on directory with the offline upstream."""
    result = process(
        [installed.python, "-I", "-c", OFFLINE, upstream.root, "--json"]
        + ["policy", *action, directory],
        directory.parent,
        upstream.environment | (env or {}),
    )
    assert result.returncode == code, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.fixture
def started(installed, tmp_path):
    """A team's new policy, started with gateseal policy init."""
    directory = tmp_path / "team ü" / "policy"
    result = installed.cli(tmp_path, "policy", "init", str(directory))
    assert result["code"] == "initialized"
    assert sorted(result["changed"]) == [
        "README.md",
        "policy.lock",
        "policy.toml",
        "rules/betterleaks.toml",
        "rules/ruff.toml",
    ]
    return directory


def lock_document(directory):
    return json.loads((directory / "policy.lock").read_bytes())


def test_init_writes_the_bundled_policy_that_checks(installed, started):
    for name in ("policy.toml", "policy.lock", "README.md", "rules/ruff.toml"):
        assert (started / name).read_bytes() == (bundled_root() / name).read_bytes()
    checked = installed.cli(started, "policy", "check", str(started))
    assert checked["code"] == "consistent"


def test_init_never_writes_into_a_directory_with_files(installed, tmp_path):
    kept = tmp_path / "policy" / "notes.txt"
    kept.parent.mkdir()
    kept.write_text("ours\n")
    refused = installed.cli(tmp_path, "policy", "init", str(kept.parent), code=2)
    assert refused["code"] == "directory_not_empty"
    assert sorted(kept.parent.iterdir()) == [kept]


def test_lock_resolves_every_reviewed_version_from_upstream(
    installed, upstream, started
):
    (started / "policy.lock").unlink()
    locked = policy_command(installed, upstream, started, "lock")
    assert (locked["code"], locked["changed"]) == ("locked", ["policy.lock"])
    components = lock_document(started)["components"]
    hooks = components["pre-commit-hooks"]["versions"]
    assert hooks["5.0.0"] == {"revision": upstream.commit(HOOKS, "v5.0.0")}
    assert hooks["6.0.0"] == {"revision": upstream.commit(HOOKS, "v6.0.0")}
    platforms = components["betterleaks"]["versions"]["1.9.0"]["platforms"]
    name = "betterleaks_1.9.0_darwin_arm64.tar.gz"
    assert platforms["macos-arm64"] == {
        "executable": "betterleaks",
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
        "url": f"https://{RELEASES}/v1.9.0/{name}",
    }
    assert installed.cli(started, "policy", "check", str(started))["code"] == (
        "consistent"
    )
    again = policy_command(installed, upstream, started, "lock")
    assert (again["code"], again["changed"]) == ("locked", [])


def test_a_moved_tag_never_rewrites_a_reviewed_revision(installed, upstream, started):
    """Accepting a moved tag of the pinned version takes a new lock."""
    (started / "policy.lock").unlink()
    policy_command(installed, upstream, started, "lock")
    before = (started / "policy.lock").read_bytes()
    first = upstream.commit(HOOKS, "v6.0.0")
    repository = upstream.repository(HOOKS)
    git(repository, "commit", "-q", "--allow-empty", "-m", "Moved")
    git(repository, "tag", "-f", "v6.0.0")
    moved = upstream.commit(HOOKS, "v6.0.0")
    refused = policy_command(installed, upstream, started, "lock", code=2)
    assert refused["code"] == "upstream_changed"
    message = refused["diagnostics"][0]["message"]
    assert "pre-commit-hooks 6.0.0" in message
    assert first in message and moved in message
    assert (started / "policy.lock").read_bytes() == before
    (started / "policy.lock").unlink()
    policy_command(installed, upstream, started, "lock")
    accepted = lock_document(started)["components"]
    expected = json.loads(before)["components"]
    expected["pre-commit-hooks"]["versions"]["6.0.0"] = {"revision": moved}
    assert accepted == expected


def test_a_replaced_checksum_never_rewrites_a_recorded_digest(
    installed, upstream, started
):
    (started / "policy.lock").unlink()
    policy_command(installed, upstream, started, "lock")
    before = (started / "policy.lock").read_bytes()
    checksums = upstream.root / RELEASES / "v1.8.0" / "checksums.txt"
    old = hashlib.sha256(b"betterleaks_1.8.0_linux_x64.tar.gz").hexdigest()
    checksums.write_text(checksums.read_text().replace(old, "e" * 64))
    refused = policy_command(installed, upstream, started, "lock", code=2)
    assert refused["code"] == "upstream_changed"
    message = refused["diagnostics"][0]["message"]
    assert "betterleaks 1.8.0" in message and old in message and "e" * 64 in message
    assert (started / "policy.lock").read_bytes() == before


def review(directory, component, version):
    path = directory / "policy.toml"
    path.write_text(
        path.read_text() + f'\n[components.{component}.versions."{version}"]\n'
    )


@pytest.mark.parametrize(
    "break_upstream,code",
    [
        (lambda root: None, "upstream_tag_missing"),
        (
            lambda root: release(root, "1.10.0", ("linux_x64",)),
            "upstream_checksum_missing",
        ),
        (
            lambda root: (
                release(root, "1.10.0") / "betterleaks_1.10.0_linux_x64.tar.gz"
            ).write_bytes(b"replaced"),
            "upstream_checksum_mismatch",
        ),
        (
            lambda root: (release(root, "1.10.0") / "checksums.txt").unlink(),
            "upstream_asset_missing",
        ),
    ],
    ids=["missing tag", "platform without checksum", "mismatch", "no checksum file"],
)
def test_lock_writes_nothing_when_upstream_lacks_a_fact(
    installed, upstream, started, break_upstream, code
):
    (started / "policy.lock").unlink()
    policy_command(installed, upstream, started, "lock")
    break_upstream(upstream.root)
    if code == "upstream_tag_missing":
        review(started, "ruff", "0.17.2")
    else:
        review(started, "betterleaks", "1.10.0")
    before = (started / "policy.lock").read_bytes()
    refused = policy_command(installed, upstream, started, "lock", code=2)
    assert refused["code"] == code, refused
    assert (started / "policy.lock").read_bytes() == before


def unreview(directory):
    path = directory / "policy.toml"
    version = '[components.pre-commit-hooks.versions."5.0.0"]\n\n'
    path.write_text(path.read_text().replace(version, ""))


def edit(directory):
    path = directory / "policy.lock"
    executable = '"executable": "betterleaks"'
    path.write_text(path.read_text().replace(executable, '"executable": "bl"', 1))


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda directory: (directory / "policy.lock").unlink(), "policy_lock_missing"),
        (lambda directory: review(directory, "ruff", "0.17.0"), "policy_lock_stale"),
        (edit, "policy_lock_edited"),
        (unreview, "policy_lock_unreviewed"),
    ],
    ids=["missing", "stale", "edited", "unreviewed"],
)
def test_check_refuses_a_lock_that_disagrees(installed, started, change, code):
    change(started)
    refused = installed.cli(started, "policy", "check", str(started), code=2)
    assert (refused["code"], refused["next_steps"]) == (code, [STEP])


def test_check_refuses_ruff_rules_that_set_the_target_version(installed, started):
    rules = started / "rules" / "ruff.toml"
    rules.write_bytes(b'target-version = "py38"\n' + rules.read_bytes())
    refused = installed.cli(started, "policy", "check", str(started), code=2)
    assert refused["code"] == "invalid_field"
    assert refused["diagnostics"][0]["message"] == (
        "The ruff configuration sets target-version, which "
        "choices.python.target_version sets for each repository."
    )


def publish(team, tag):
    git(team, "add", "-A")
    git(team, "commit", "-q", "-m", f"Release {tag}")
    git(team, "tag", "-a", "-m", tag, tag)


def adopt(installed, consumer, team, tag, code=0):
    return installed.cli(
        consumer,
        "configure",
        "--capability",
        "hygiene",
        "--policy",
        TEAM,
        "--policy-path",
        "policy",
        "--policy-tag",
        tag,
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
        code=code,
        env=with_git_config(
            installed.environment, {f"url.{team.as_uri()}.insteadOf": TEAM}
        ),
    )


def test_a_started_policy_is_adopted_and_a_stale_release_is_refused(
    installed, started, tmp_path
):
    """Adoption loads the release through the same check as policy check.

    Configure, activation and check load every policy through that check, so
    a release whose lock disagrees never reaches a Lock.
    """
    team = started.parent
    git(team, "init", "-q", "--initial-branch=main")
    publish(team, "v1")
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    assert adopt(installed, consumer, team, "v1")["code"] == "configured"
    review(started, "ruff", "0.17.0")
    publish(team, "v2")
    fresh = clone(tmp_path / "origin 2", tmp_path / "consumer 2")
    refused = adopt(installed, fresh, team, "v2", code=2)
    assert (refused["code"], refused["next_steps"]) == ("policy_lock_stale", [STEP])
    assert not (fresh / "gateseal.lock").exists()


@pytest.mark.network
def test_lock_reads_the_bundled_facts_from_the_real_upstream(installed, started):
    """Resolve every bundled version from GitHub, downloading each archive."""
    (started / "policy.lock").unlink()
    locked = installed.cli(started, "policy", "lock", str(started))
    assert locked["changed"] == ["policy.lock"]
    assert (started / "policy.lock").read_bytes() == (
        bundled_root() / "policy.lock"
    ).read_bytes()


@pytest.fixture
def locked(installed, upstream, started):
    """The started policy, locked against the offline upstream."""
    (started / "policy.lock").unlink()
    policy_command(installed, upstream, started, "lock")
    return started


POLICY_FILES = ("policy.toml", "policy.lock")


def files(directory):
    return {name: (directory / name).read_bytes() for name in POLICY_FILES}


def test_add_reviews_a_version_and_pin_makes_it_the_pin(installed, upstream, locked):
    release(upstream.root, "1.10.0")
    before = files(locked)
    added = policy_command(installed, upstream, locked, "add", "betterleaks", "1.10.0")
    assert (added["code"], added["changed"]) == ("added", list(POLICY_FILES))
    assert "gateseal policy pin betterleaks 1.10.0" in added["next_steps"][0]
    newest = '[components.betterleaks.versions."1.9.0"]'
    assert (locked / "policy.toml").read_bytes() == before["policy.toml"].replace(
        newest.encode(),
        f'[components.betterleaks.versions."1.10.0"]\n\n{newest}'.encode(),
    )
    record = lock_document(locked)["components"]["betterleaks"]["versions"]["1.10.0"]
    name = "betterleaks_1.10.0_linux_x64.tar.gz"
    assert record["platforms"]["linux-x64"] == {
        "executable": "betterleaks",
        "sha256": hashlib.sha256(name.encode()).hexdigest(),
        "url": f"https://{RELEASES}/v1.10.0/{name}",
    }
    again = policy_command(installed, upstream, locked, "add", "betterleaks", "1.10.0")
    assert (again["code"], again["changed"]) == ("added", [])
    reviewed = files(locked)
    pinned = installed.cli(
        locked, "policy", "pin", "betterleaks", "1.10.0", str(locked)
    )
    assert (pinned["code"], pinned["changed"]) == ("pinned", ["policy.toml"])
    assert "1.9.0 stays reviewed" in pinned["diagnostics"][0]["message"]
    assert files(locked) == {
        "policy.toml": reviewed["policy.toml"].replace(
            b'pin = "1.9.0"', b'pin = "1.10.0"'
        ),
        "policy.lock": reviewed["policy.lock"],
    }
    again = installed.cli(locked, "policy", "pin", "betterleaks", "1.10.0", str(locked))
    assert (again["code"], again["changed"]) == ("pinned", [])
    assert installed.cli(locked, "policy", "check", str(locked))["code"] == "consistent"


COMMENTED = """\
# Our team's policy. gateseal reads this file; people edit it.
schema = 4
release = "team-2026.11"
minimum_compiler = "0.1.0"

[capabilities.hygiene]
tools = ["pre-commit-hooks"]

[capabilities.python]
tools = ["ruff"]

[capabilities.secrets]
tools = ["betterleaks"]

[tools.pre-commit-hooks]
hook_ids = ["trailing-whitespace", "end-of-file-fixer", "check-yaml"]

[tools.ruff]
hook_ids = ["ruff-check", "ruff-format"]
configuration = "rules/ruff.toml"

[tools.betterleaks]
configuration = "rules/betterleaks.toml"

[choices.hygiene.exclude]
default = []

[components.pre-commit-hooks]
pin = "6.0.0"

[components.pre-commit-hooks.versions."6.0.0"]

[components.pre-commit-hooks.versions."5.0.0"]

[components.ruff]
pin = "0.17.1"

[components.ruff.versions."0.17.1"]

# Betterleaks: read each release's notes before review.
[components.betterleaks]
pin = 'BEFORE'  # Raised after the October review.

# Reviewed on 2026-10-20.
[components.betterleaks.versions."1.9.0"]

# Kept for repositories that still scan with 1.8.
[components.betterleaks.versions."1.8.0"]"""


def test_add_and_pin_keep_the_authors_comments_and_order(installed, upstream, locked):
    """The two commands change only their own lines of a commented policy.toml."""
    path = locked / "policy.toml"
    path.write_text(COMMENTED.replace("BEFORE", "1.9.0"))
    assert installed.cli(locked, "policy", "check", str(locked))["code"] == "consistent"
    release(upstream.root, "1.10.0")
    policy_command(installed, upstream, locked, "add", "betterleaks", "1.10.0")
    installed.cli(locked, "policy", "pin", "betterleaks", "1.10.0", str(locked))
    assert path.read_text() == COMMENTED.replace("BEFORE", "1.10.0").replace(
        "# Reviewed on",
        '[components.betterleaks.versions."1.10.0"]\n\n# Reviewed on',
    )
    assert installed.cli(locked, "policy", "check", str(locked))["code"] == "consistent"


@pytest.mark.parametrize(
    "break_upstream,tool,code",
    [
        (lambda root: None, "ruff 0.17.2", "upstream_tag_missing"),
        (
            lambda root: release(root, "1.10.0", ("linux_x64",)),
            "betterleaks 1.10.0",
            "upstream_checksum_missing",
        ),
        (
            lambda root: (
                release(root, "1.10.0") / "betterleaks_1.10.0_linux_x64.tar.gz"
            ).write_bytes(b"replaced"),
            "betterleaks 1.10.0",
            "upstream_checksum_mismatch",
        ),
        (
            lambda root: (release(root, "1.10.0") / "checksums.txt").unlink(),
            "betterleaks 1.10.0",
            "upstream_asset_missing",
        ),
        (lambda root: None, "betterleaks latest", "invalid_invocation"),
        (lambda root: None, "gitleaks 8.28.0", "unknown_component"),
    ],
    ids=[
        "missing tag",
        "platform without checksum",
        "mismatch",
        "no checksum file",
        "latest",
        "unknown",
    ],
)
def test_add_writes_nothing_when_a_fact_is_missing(
    installed, upstream, locked, break_upstream, tool, code
):
    break_upstream(upstream.root)
    before = files(locked)
    refused = policy_command(installed, upstream, locked, "add", *tool.split(), code=2)
    assert refused["code"] == code, refused
    if code != "upstream_checksum_mismatch" and code.startswith("upstream_"):
        assert refused["next_steps"] == [
            f"Check that {tool} is released, then run gateseal policy add {tool} again."
        ]
    assert files(locked) == before


def test_add_never_replaces_an_edit_saved_while_it_runs(installed, upstream, locked):
    """An author's edit saved during the downloads stays, and add writes nothing."""
    release(upstream.root, "1.10.0")
    path, lock = locked / "policy.toml", (locked / "policy.lock").read_bytes()
    refused = policy_command(
        installed,
        upstream,
        locked,
        "add",
        "betterleaks",
        "1.10.0",
        code=2,
        env={"EDIT_DURING_DOWNLOAD": str(path)},
    )
    assert (refused["code"], refused["next_steps"]) == (
        "policy_changed",
        ["Run the command again."],
    )
    assert path.read_text().endswith("# Edited during a download.\n")
    assert "1.10.0" not in path.read_text()
    assert (locked / "policy.lock").read_bytes() == lock


SLOW_WRITES = """
import os, sys, time
from gateseal.cli import main

replace = os.replace


def slowly(*arguments, **options):
    time.sleep(2)
    return replace(*arguments, **options)


os.replace = slowly
raise SystemExit(main(sys.argv[1:]))
"""


def test_two_pins_at_once_never_lose_a_reported_pin(installed, started):
    """Each write takes two seconds, so both commands read before either writes.

    The second to write finds the first's edit and writes nothing.
    """

    def pin(tool_version):
        command = [installed.python, "-I", "-c", SLOW_WRITES, "--json", "policy"]
        result = process(
            [*command, "pin", *tool_version.split(), started],
            started.parent,
            installed.environment,
        )
        return tool_version, json.loads(result.stdout)["code"]

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(pin, ["pre-commit-hooks 5.0.0", "betterleaks 1.8.0"]))
    assert sorted(code for _, code in results) == ["pinned", "policy_changed"]
    text = (started / "policy.toml").read_text()
    for tool_version, code in results:
        tool, version = tool_version.split()
        pinned = f'[components.{tool}]\npin = "{version}"' in text
        assert pinned == (code == "pinned"), (results, text)


def add_requires(directory, version, requires):
    path = directory / "policy.toml"
    table = f"[components.{version}]\n"
    path.write_text(path.read_text().replace(table, f"{table}requires = {requires}\n"))


@pytest.mark.parametrize(
    "edit,arguments,code,message",
    [
        (
            lambda directory: None,
            "betterleaks 1.10.0",
            "version_unreviewed",
            "does not review betterleaks 1.10.0",
        ),
        (
            lambda d: add_requires(
                d, 'ruff.versions."0.17.1"', '{ betterleaks = "1.9.0" }'
            ),
            "betterleaks 1.8.0",
            "requires_conflict",
            "ruff 0.17.1 needs betterleaks 1.9.0 or newer, but the pins select "
            "betterleaks 1.8.0.",
        ),
        (
            lambda d: add_requires(
                d, 'betterleaks.versions."1.8.0"', '{ ruff = "0.18.0" }'
            ),
            "betterleaks 1.8.0",
            "requires_conflict",
            "betterleaks 1.8.0 needs ruff 0.18.0 or newer, but the pins select "
            "ruff 0.17.1.",
        ),
    ],
    ids=["unreviewed", "pinned requires it", "it requires a newer pin"],
)
def test_pin_refuses_an_unreviewed_or_conflicting_version(
    installed, started, edit, arguments, code, message
):
    edit(started)
    assert (
        installed.cli(started, "policy", "check", str(started))["code"] == "consistent"
    )
    before = files(started)
    refused = installed.cli(
        started, "policy", "pin", *arguments.split(), str(started), code=2
    )
    assert refused["code"] == code
    assert message in refused["diagnostics"][0]["message"]
    if code == "version_unreviewed":
        assert refused["next_steps"] == [
            "Run gateseal policy add betterleaks 1.10.0 in the policy directory first."
        ]
    assert files(started) == before


def test_add_refuses_a_policy_toml_it_cannot_edit(installed, started):
    """An inline table of versions takes a hand edit, and add writes nothing."""
    path = started / "policy.toml"
    path.write_text(
        path.read_text().replace(
            'pin = "0.17.1"\n\n[components.ruff.versions."0.17.1"]\n',
            'pin = "0.17.1"\nversions = { "0.17.1" = {} }\n',
        )
    )
    assert (
        installed.cli(started, "policy", "check", str(started))["code"] == "consistent"
    )
    before = files(started)
    refused = installed.cli(
        started, "policy", "add", "ruff", "0.18.0", str(started), code=2
    )
    assert refused["code"] == "policy_edit_unsupported"
    assert files(started) == before


@pytest.mark.parametrize("read", ["git", "download"])
@pytest.mark.parametrize(
    "action", [("lock",), ("add", "betterleaks", "1.10.0")], ids=["lock", "add"]
)
def test_a_failed_upstream_read_names_the_command_to_rerun(
    installed, upstream, locked, read, action
):
    release(upstream.root, "1.10.0")
    if read == "git":
        upstream.repository(HOOKS).rename(upstream.root / "unreachable")
    before = files(locked)
    refused = policy_command(
        installed,
        upstream,
        locked,
        *action,
        code=4,
        env={"UNREACHABLE": "1"} if read == "download" else {},
    )
    assert refused["code"] == "upstream_unavailable"
    [step] = refused["next_steps"]
    assert step.endswith(f", then rerun gateseal policy {' '.join(action)}.")
    assert files(locked) == before


@pytest.mark.network
def test_add_reviews_a_real_release(installed, started):
    """Review Betterleaks 1.8.1 from GitHub, and fail on a tag that does not exist."""
    before = lock_document(started)
    added = installed.cli(
        started, "policy", "add", "betterleaks", "1.8.1", str(started)
    )
    assert added["changed"] == ["policy.toml", "policy.lock"]
    versions = lock_document(started)["components"]["betterleaks"]["versions"]
    record = versions.pop("1.8.1")["platforms"]["macos-arm64"]
    assert record["url"] == (
        "https://github.com/betterleaks/betterleaks/releases/download/v1.8.1/"
        "betterleaks_1.8.1_darwin_arm64.tar.gz"
    )
    assert len(record["sha256"]) == 64
    assert versions == before["components"]["betterleaks"]["versions"]
    refused = installed.cli(
        started, "policy", "add", "ruff", "0.17.99", str(started), code=2
    )
    assert refused["code"] == "upstream_tag_missing"
    assert refused["next_steps"] == [
        "Check that ruff 0.17.99 is released, then run gateseal policy add ruff "
        "0.17.99 again."
    ]
