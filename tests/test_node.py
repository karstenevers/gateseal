"""A policy pins one Node, which every tool on Node runs on.

gateseal policy lock reads each platform's archive and SHA-256 from Node's
SHASUMS256.txt, and every load reads them back from the Policy Lock. A fake
nodejs.org serves the release files from a dictionary.
"""

import json

import pytest
from gateseal import authoring, upgrade
from gateseal.platforms import TARGETS
from gateseal.policy import load_policy_tree
from gateseal.tools.mise import mise_request
from gateseal.tools.model import MissingUpstream
from gateseal.tools.node import (
    ARCHIVES,
    Node,
    NodeArchive,
    Tree,
    lock_node,
    package_name,
    read_node,
    read_tree,
)
from gateseal.tools.release_binary import checked_programs
from gateseal.validation import canonical
from node_policy import (
    DIST,
    LINUX,
    MACOS,
    SHASUMS,
    TABLE,
    VERSION,
    Releases,
    docs,
    lockfile,
    node_files,
    node_record,
    releases,  # noqa: F401
)
from node_policy import POLICY as DOCS
from stand_ins import executable
from test_lock_configure import refused
from test_team_tools import POLICY, consumer, hook_tool, team, upstream  # noqa: F401


def test_node_has_an_archive_for_every_platform_gateseal_supports():
    assert set(ARCHIVES) == set(TARGETS.values())


def test_policy_lock_checks_each_archive_against_the_checksum_file():
    served = Releases(node_files())
    assert lock_node(VERSION, None, served) == node_record()
    assert served.downloaded == [SHASUMS, DIST + LINUX, DIST + MACOS]


def test_a_recorded_archive_is_kept_without_a_download():
    served = Releases(node_files())
    assert lock_node(VERSION, node_record(), served) == node_record()
    assert served.downloaded == [SHASUMS]


def test_a_record_of_another_node_is_dropped():
    served = Releases(node_files())
    assert lock_node(VERSION, node_record("24.22.2"), served) == node_record()
    assert served.downloaded == [SHASUMS, DIST + LINUX, DIST + MACOS]


def test_a_recorded_platform_that_gateseal_does_not_prepare_is_dropped():
    recorded = node_record()
    recorded["platforms"]["linux-arm64"] = {"sha256": "0" * 64, "url": "x"}
    served = Releases(node_files())
    assert lock_node(VERSION, recorded, served) == node_record()
    assert served.downloaded == [SHASUMS]


def test_a_changed_checksum_of_a_recorded_archive_fails():
    recorded = node_record()
    recorded["platforms"]["linux-x64"]["sha256"] = "0" * 64
    failure = refused(lock_node, VERSION, recorded, Releases(node_files()))
    assert failure.code == "upstream_changed"
    assert str(failure).startswith(
        f'policy.lock records {{"linux-x64": {{"sha256": "{"0" * 64}"'
    )
    assert f"for node {VERSION}, but SHASUMS256.txt of node {VERSION} now gives" in (
        str(failure)
    )


@pytest.mark.parametrize(
    "recorded",
    [
        {"version": VERSION},
        {"version": VERSION, "platforms": []},
        {"version": VERSION, "platforms": {}, "sha256": "0" * 64},
    ],
    ids=["no platforms", "platforms not a table", "another field"],
)
def test_an_unreadable_record_of_the_same_node_fails(recorded):
    failure = refused(lock_node, VERSION, recorded, Releases(node_files()))
    assert failure.code == "upstream_changed"


def test_a_release_without_a_checksum_file_is_missing():
    files = node_files()
    del files[SHASUMS]
    failure = refused(lock_node, VERSION, None, Releases(files))
    assert isinstance(failure, MissingUpstream)
    assert (failure.code, failure.name, failure.version) == (
        "upstream_asset_missing",
        "node",
        VERSION,
    )
    assert str(failure) == f"The node {VERSION} release has no SHASUMS256.txt."


def test_a_checksum_file_that_lists_no_archive_fails():
    files = node_files()
    files[SHASUMS] = b"\n".join(
        line for line in files[SHASUMS].splitlines() if b"linux" not in line
    )
    failure = refused(lock_node, VERSION, None, Releases(files))
    assert failure.code == "upstream_checksum_missing"
    assert str(failure) == (
        f"SHASUMS256.txt of node {VERSION} lists no {LINUX}, the linux-x64 archive."
    )


def test_an_archive_without_its_listed_checksum_fails():
    files = node_files()
    files[DIST + MACOS] = b"other bytes"
    failure = refused(lock_node, VERSION, None, Releases(files))
    assert failure.code == "upstream_checksum_mismatch"
    assert str(failure).startswith(f"{DIST}{MACOS} does not have the SHA-256")


def test_a_listed_archive_the_server_lacks_is_missing():
    files = node_files()
    del files[DIST + MACOS]
    failure = refused(lock_node, VERSION, None, Releases(files))
    assert failure.code == "upstream_asset_missing"


def test_a_record_reads_back_as_the_node_that_starts_programs():
    node = read_node(VERSION, node_record())
    record = node_record()["platforms"]
    assert node == Node(
        VERSION,
        {
            platform: NodeArchive(archive["url"], archive["sha256"])
            for platform, archive in record.items()
        },
    )
    request = mise_request("node", VERSION, node.platforms)
    assert request.startswith("http:node-")
    assert request.endswith(
        f"[platforms_linux_x64_url={DIST}{LINUX},platforms_linux_x64_checksum="
        f"sha256:{record['linux-x64']['sha256']},platforms_macos_arm64_url="
        f"{DIST}{MACOS},platforms_macos_arm64_checksum="
        f"sha256:{record['macos-arm64']['sha256']}]@{VERSION}"
    )


ESLINT = {"eslint": "10.15.0", "@typescript-eslint/parser": "8.73.0"}


def test_a_tree_starts_the_program_by_its_path_in_the_package():
    record = lockfile(ESLINT)
    tree = read_tree(record, ESLINT, "eslint", "eslint", "eslint 10.15.0")
    assert tree == Tree(canonical(record), "node_modules/eslint/bin/cli.js")


MISE = """#!/bin/sh
case $2 in
version) cat "$FAKE/version" 2>/dev/null || echo "2026.10.19 linux-x64" ;;
where) [ ! -e "$FAKE/uninstalled" ] || exit 1; echo "$FAKE/node" ;;
exec) shift 4; exec "$@" ;;
esac
"""
"""mise --no-config version, where INSTALL, and exec REQUEST -- PROGRAM ARGS.
Beside a file version, it reports the version that the file holds. Beside a
file uninstalled, where finds no install."""

NPM_LOCK = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE/arguments"
[ ! -e "$FAKE/report" ] || { cat "$FAKE/report" >&2; exit 1; }
printf '{"name": "x", "lockfileVersion": 3, "packages": {"": %s}}' \\
  "$(cat "$4/package.json")" > "$4/package-lock.json"
"""
"""npm install --package-lock-only --prefix FOLDER ...: it locks FOLDER's
package.json, or prints the file report and fails beside it."""


def lock_on_node(tmp_path, monkeypatch, report=None):
    """Lock Prettier 3.10.1 with a stand-in mise and npm, and return the fakes."""
    fake = tmp_path / "fake"
    executable(fake / "bin/mise", MISE)
    executable(fake / "node/bin/node", '#!/bin/sh\nexec /bin/sh "$@"\n')
    executable(fake / "node/bin/npm", NPM_LOCK)
    if report is not None:
        (fake / "report").write_text(report)
    monkeypatch.setenv("PATH", f"{fake / 'bin'}:/usr/bin:/bin")
    monkeypatch.setenv("FAKE", str(fake))
    (tmp_path / "scratch").mkdir()
    npm = authoring.Releases("git", tmp_path / "scratch", "gateseal policy lock")
    npm.node = read_node(VERSION, node_record())
    return lambda: npm.lockfile({"prettier": "3.10.1"}, "prettier", "3.10.1"), fake


def test_lock_runs_npm_on_the_pinned_node_from_an_empty_configuration(
    tmp_path, monkeypatch
):
    lock, fake = lock_on_node(tmp_path, monkeypatch)
    assert lock() == {
        "lockfileVersion": 3,
        "packages": {"": {"dependencies": {"prettier": "3.10.1"}}},
    }
    arguments = (fake / "arguments").read_text().splitlines()
    folder = arguments[3]
    assert folder.startswith(f"{tmp_path / 'scratch'}/")
    assert arguments == [
        "install",
        "--package-lock-only",
        "--prefix",
        folder,
        "--userconfig",
        f"{folder}/.npmrc",
        "--globalconfig",
        f"{folder}/.npmrc-global",
        "--cache",
        f"{folder}/.npm",
        "--ignore-scripts",
        "--no-audit",
        "--no-update-notifier",
        "--logs-max=0",
    ]


def test_npm_gets_the_real_path_of_a_folder_behind_a_symlink(tmp_path, monkeypatch):
    lock, fake = lock_on_node(tmp_path, monkeypatch)
    (tmp_path / "scratch").rename(tmp_path / "real scratch")
    (tmp_path / "scratch").symlink_to(tmp_path / "real scratch")
    lock()
    folder = (fake / "arguments").read_text().splitlines()[3]
    assert folder.startswith(f"{tmp_path / 'real scratch'}/")


@pytest.mark.parametrize(
    ("report", "code"),
    [
        ("npm error code E404", "upstream_release_missing"),
        ("npm error code ETARGET", "upstream_release_missing"),
        ("npm error code ECONNREFUSED", "upstream_unavailable"),
    ],
)
def test_a_failed_npm_names_the_tool_version_and_npm_s_report(
    tmp_path, monkeypatch, report, code
):
    lock, _ = lock_on_node(tmp_path, monkeypatch, f"{report}\n")
    failure = refused(lock)
    assert failure.code == code
    assert isinstance(failure, MissingUpstream) == (code != "upstream_unavailable")
    assert (
        str(failure) == f"npm cannot resolve the packages of prettier 3.10.1: {report}"
    )
    if code == "upstream_unavailable":
        assert failure.exit_code == 4
        assert failure.next_step == (
            "Check that npm reaches its registry, then rerun gateseal policy lock."
        )


def test_lock_without_the_pinned_node_names_it_and_runs_no_npm(tmp_path, monkeypatch):
    lock, fake = lock_on_node(tmp_path, monkeypatch)
    (fake / "uninstalled").touch()
    failure = refused(lock)
    assert (failure.code, failure.exit_code) == ("upstream_unavailable", 4)
    assert str(failure) == (
        f"mise has no install of Node {VERSION}, the pinned Node. "
        "Turn the auto-install of mise on."
    )
    assert failure.next_step == "Then rerun gateseal policy lock."
    assert not (fake / "arguments").exists()


NPM_CI = """#!/bin/sh
printf '%s\\n' "$@" > "$FAKE/arguments"
[ ! -e "$FAKE/report" ] || { cat "$FAKE/report" >&2; exit 1; }
cp "$3/package.json" "$3/package-lock.json" "$FAKE/"
"""
"""npm ci --prefix FOLDER ...: it keeps FOLDER's package files, or fails."""

PROGRAM = """printf '%s\\n' "$@" > "$FAKE/node-arguments"
[ ! -e "$FAKE/node-report" ] || { cat "$FAKE/node-report" >&2; exit 1; }
printf '{"read": true}'
"""
"""The program FOLDER ARGUMENT, which the stand-in node runs with sh: it prints
JSON or fails."""

LOCKED = {"packages": {"": {"dependencies": {"eslint": "10.15.0"}}}}


def evaluate_on_node(tmp_path, monkeypatch, report=None, failure=None):
    """Run a program in an install of ESLint 10.15.0 with stand-ins, and the fakes."""
    lock, fake = lock_on_node(tmp_path, monkeypatch, report)
    executable(fake / "node/bin/npm", NPM_CI)
    if failure is not None:
        (fake / "node-report").write_text(failure)
    npm = authoring.Releases("git", tmp_path / "scratch", "gateseal policy lock")
    npm.node = read_node(VERSION, node_record())
    program = tmp_path / "program.mjs"
    program.write_text(PROGRAM)
    return lambda: npm.evaluate(LOCKED, program, {"a": [1]}, "eslint", "10.15.0"), fake


def test_evaluate_installs_the_lockfile_and_runs_the_program_on_node(
    tmp_path, monkeypatch
):
    evaluate, fake = evaluate_on_node(tmp_path, monkeypatch)
    assert evaluate() == {"read": True}
    assert json.loads((fake / "package-lock.json").read_bytes()) == LOCKED
    assert json.loads((fake / "package.json").read_bytes()) == {
        "dependencies": {"eslint": "10.15.0"}
    }
    folder = (fake / "arguments").read_text().splitlines()[2]
    assert (fake / "arguments").read_text().splitlines()[:3] == [
        "ci",
        "--prefix",
        folder,
    ]
    assert (fake / "node-arguments").read_text().splitlines() == [
        folder,
        '{"a": [1]}',
    ]


def test_evaluate_fails_as_npm_or_the_program_fails(tmp_path, monkeypatch):
    evaluate, _ = evaluate_on_node(tmp_path, monkeypatch, "npm error code E404\n")
    missing = refused(evaluate)
    assert (missing.code, str(missing)) == (
        "upstream_release_missing",
        "npm cannot install the packages of eslint 10.15.0: npm error code E404",
    )
    evaluate, _ = evaluate_on_node(tmp_path / "node", monkeypatch, failure="boom\n")
    failure = refused(evaluate)
    assert (failure.code, failure.exit_code, str(failure), failure.next_step) == (
        "tool_failed",
        4,
        "Node cannot run program.mjs on the packages of eslint 10.15.0: boom",
        f"Check that the packages of eslint 10.15.0 load on Node {VERSION}, then "
        "rerun gateseal policy lock.",
    )


def test_lock_needs_a_mise_that_installs_node_by_url(tmp_path, monkeypatch):
    lock, fake = lock_on_node(tmp_path, monkeypatch)
    (fake / "version").write_text("2026.10.18 linux-x64\n")
    assert refused(lock).code == "mise_version_mismatch"
    assert not (fake / "arguments").exists()


def test_the_lock_records_node_as_mise_installs_it_and_checks_no_program():
    entry = read_node(VERSION, node_record()).lock_entry()
    record = node_record()["platforms"]
    assert entry == (
        "native_artifacts",
        "node",
        {
            "version": VERSION,
            "minimum_mise": "2026.10.19",
            "platforms": {
                platform: archive | {"executable": "bin/node"}
                for platform, archive in record.items()
            },
        },
    )
    assert checked_programs({"node": entry[2]}, "linux-x64") == ()


def stale(version, record):
    failure = refused(read_node, version, record)
    assert failure.next_step == "Run gateseal policy lock in the policy directory."
    return failure


def test_a_record_of_another_node_or_other_platforms_is_stale():
    failure = stale("24.22.4", node_record())
    assert failure.code == "policy_lock_stale"
    assert str(failure) == (
        f"policy.lock records node {VERSION} for linux-x64, macos-arm64, but "
        "gateseal prepares node 24.22.4, which policy.toml pins, for linux-x64, "
        "macos-arm64."
    )
    record = node_record()
    del record["platforms"]["linux-x64"]
    assert stale(VERSION, record).code == "policy_lock_stale"
    record["platforms"] = {}
    assert "for no platform, but" in str(stale(VERSION, record))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("url", "https://example.invalid/node.tar.gz", f"must be {DIST}{LINUX}."),
        ("sha256", "0" * 63, "must be a lowercase SHA-256."),
    ],
)
def test_an_edited_archive_record_names_its_field(field, value, message):
    record = node_record()
    record["platforms"]["linux-x64"][field] = value
    failure = refused(read_node, VERSION, record)
    assert failure.code == "invalid_field"
    assert str(failure) == f"node.platforms.linux-x64.{field} {message}"


def test_an_archive_record_holds_exactly_its_url_and_sha256():
    record = node_record()
    record["platforms"]["macos-arm64"]["executable"] = "bin/node"
    assert refused(read_node, VERSION, record).code == "invalid_field"


@pytest.mark.parametrize(
    "name", ["prettier", "@typescript-eslint/parser", "lodash.merge", "a-b_c", "@x/y.z"]
)
def test_a_package_name_may_be_scoped(name):
    assert package_name(name, "field") == name


@pytest.mark.parametrize(
    "name",
    [
        "Prettier",
        "@scope",
        "@scope/",
        "prettier@3",
        "../prettier",
        "-x",
        ".x",
        "a b",
        "",
    ],
)
def test_a_package_name_holds_nothing_npm_or_a_hook_entry_would_read_otherwise(name):
    failure = refused(package_name, name, "tools.x.source.package")
    assert failure.code == "invalid_field"
    assert str(failure).startswith("tools.x.source.package must be")


def test_a_policy_without_a_tool_on_node_pins_no_node(tmp_path, upstream):  # noqa: F811
    policy = POLICY.replace(
        'minimum_compiler = "0.1.0"\n',
        f'minimum_compiler = "0.1.0"\nnode = "{VERSION}"\n',
    )
    root = team(tmp_path, policy)
    failure = refused(authoring.lock, root)
    assert failure.code == "invalid_field"
    assert (
        str(failure) == "policy.toml pins node, but no tool of the policy runs on Node."
    )
    assert failure.next_step == "Remove node from policy.toml."
    assert not (root / "policy.lock").exists()


@pytest.mark.parametrize("value", ['"24"', '"v24.22.3"', "24"])
def test_node_is_a_stable_release(tmp_path, upstream, value):  # noqa: F811
    policy = POLICY.replace(
        'minimum_compiler = "0.1.0"\n', f'minimum_compiler = "0.1.0"\nnode = {value}\n'
    )
    failure = refused(authoring.lock, team(tmp_path, policy))
    assert failure.code == "invalid_field"
    assert str(failure).startswith("node must be")


def test_no_tool_can_take_the_name_of_the_node(tmp_path, upstream):  # noqa: F811
    policy = POLICY.replace("tools.yamllint]", "tools.node]")
    root = team(
        tmp_path, policy, {"tools/node.toml": hook_tool("https://x.invalid/y", "y")}
    )
    failure = refused(authoring.lock, root)
    assert failure.code == "reserved_tool_name"
    assert str(failure) == (
        "The policy names the tool node, but node names the Node that node in "
        "policy.toml pins."
    )


def test_policy_lock_records_the_node_once_for_the_policy(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path)
    assert authoring.lock(root).code == "locked"
    lock = json.loads((root / "policy.lock").read_bytes())
    assert sorted(lock) == ["components", "generated_by", "node"]
    assert lock["node"] == node_record()
    tool = load_policy_tree(root).tools["prettier-docs"]
    assert tool.node == read_node(VERSION, node_record())
    assert authoring.check(root).code == "consistent"
    releases.downloaded.clear()
    assert authoring.lock(root).changed == ()
    assert releases.downloaded == [SHASUMS]


def test_a_policy_with_a_tool_on_node_needs_node(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path, DOCS.replace(f'node = "{VERSION}"\n', ""))
    failure = refused(authoring.lock, root)
    assert failure.code == "invalid_field"
    assert (
        str(failure) == "policy.toml needs node, the version of Node for prettier-docs."
    )
    assert failure.next_step == (
        'Add node = "<major.minor.patch>" at the top of policy.toml, then run '
        "gateseal policy lock."
    )
    assert not (root / "policy.lock").exists()


def edit_lock(root, change):
    lock = json.loads((root / "policy.lock").read_bytes())
    change(lock)
    (root / "policy.lock").write_bytes(canonical(lock))


def test_a_lock_without_the_node_that_policy_toml_pins_is_stale(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path)
    authoring.lock(root)
    edit_lock(root, lambda lock: lock.pop("node"))
    failure = refused(load_policy_tree, root)
    assert failure.code == "policy_lock_stale"
    assert str(failure) == (
        f"policy.lock has no facts for node {VERSION}, which policy.toml pins."
    )


def test_a_node_record_without_a_pin_is_unreviewed(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path)
    authoring.lock(root)
    (root / "policy.toml").write_text(
        DOCS.replace(f'node = "{VERSION}"\n', "").replace(TABLE, "")
    )
    edit_lock(root, lambda lock: lock["components"].pop("prettier-docs"))
    failure = refused(load_policy_tree, root)
    assert failure.code == "policy_lock_unreviewed"
    assert str(failure) == "policy.lock records node, but policy.toml pins none."


def test_an_edited_node_record_is_an_edited_lock(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path)
    authoring.lock(root)
    edit_lock(root, lambda lock: lock["node"]["platforms"]["linux-x64"].update(url="x"))
    failure = refused(load_policy_tree, root)
    assert failure.code == "policy_lock_edited"
    assert (
        str(failure)
        == f"policy.lock: node.platforms.linux-x64.url must be {DIST}{LINUX}."
    )


def test_a_node_bump_is_an_edit_and_a_lock(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path)
    authoring.lock(root)
    before = json.loads((root / "policy.lock").read_bytes())
    (root / "policy.toml").write_text(DOCS.replace(VERSION, "24.22.4"))
    assert refused(authoring.check, root).code == "policy_lock_stale"
    releases.files |= node_files("24.22.4")
    assert authoring.lock(root).code == "locked"
    after = json.loads((root / "policy.lock").read_bytes())
    assert after == before | {"node": node_record("24.22.4")}
    assert authoring.check(root).code == "consistent"


def test_the_lock_records_the_node_and_upgrade_reports_its_move(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path)
    authoring.lock(root)
    _, before = consumer(load_policy_tree(root), ("hygiene", "docs"))
    native = before["native_artifacts"]
    assert native == {"node": read_node(VERSION, node_record()).lock_entry()[2]}
    (root / "policy.toml").write_text(DOCS.replace(VERSION, "24.22.4"))
    releases.files |= node_files("24.22.4")
    authoring.lock(root)
    _, after = consumer(load_policy_tree(root), ("hygiene", "docs"))
    entries = upgrade.report(before, after, frozenset())
    assert [e for e in entries if e.get("target") == "node"] == [
        {
            "severity": "info",
            "category": "upgrade",
            "target": "node",
            "state": "upgrade",
            "from": VERSION,
            "to": "24.22.4",
            "message": f"node {VERSION} to 24.22.4: upgrade.",
        }
    ]
    _, hygiene = consumer(load_policy_tree(root), ("hygiene",))
    assert hygiene["native_artifacts"] == {}
