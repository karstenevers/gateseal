"""A policy runs a tool from npm on its Node: the npm-package kind.

The team policy of node_policy formats docs with Prettier and two plugins
that policy.toml pins as the tool's packages. A fake upstream answers where
gateseal policy lock and add would ask nodejs.org and npm.
"""

import json
import shlex
import subprocess
from dataclasses import replace

import pytest
from gateseal import authoring, checks, lock
from gateseal.ci import Scope
from gateseal.lock import describe
from gateseal.policy import load_policy_tree, review_policy
from gateseal.processes import Timed
from gateseal.tools import load_definition
from gateseal.tools.model import Origin
from gateseal.validation import canonical
from node_policy import (
    DEFINITION,
    PACKAGES,
    PINS,
    PRETTIER,
    RULES,
    VERSION,
    docs,
    lockfile,
    releases,  # noqa: F401
)
from node_policy import POLICY as DOCS
from stand_ins import executable
from test_health import NEED, check, gateseal, hygiene, machine  # noqa: F401
from test_lock_configure import refused
from test_team_tools import CAVEAT, POLICY, consumer, team, upstream  # noqa: F401

DEPENDENCIES = {"prettier": PRETTIER, **PACKAGES}
RECORD = {"lockfile": lockfile(DEPENDENCIES)}
STALE_LOCK = "Run gateseal policy lock in the policy directory."
ARGS = (
    "--config .gateseal/prettier-docs.toml --no-editorconfig --ignore-path "
    "/dev/null --write --ignore-unknown --"
)
GENERATED = (
    r"^(?:\.gateseal(?:/|$)|\.github/workflows/gateseal\.yml(?:/|$)|"
    r"\.pre-commit-config\.yaml(?:/|$)|azure-pipelines-gateseal\.yml(?:/|$)|"
    r"gateseal\.lock(?:/|$)|gateseal\.toml(?:/|$))"
)


def locked_policy(tmp_path, policy=DOCS, definition=DEFINITION):
    root = docs(tmp_path, policy, definition)
    authoring.lock(root)
    return root


def lock_records(root):
    return json.loads((root / "policy.lock").read_bytes())["components"]


def edit_record(root, edit):
    """Rewrite the Policy Lock record of prettier-docs at its pin with edit."""
    lock = json.loads((root / "policy.lock").read_bytes())
    versions = lock["components"]["prettier-docs"]["versions"]
    versions[PRETTIER] = edit(versions[PRETTIER])
    (root / "policy.lock").write_bytes(canonical(lock))


def hooks(locked):
    """Return the hooks of each repository in the generated configuration."""
    config = json.loads(locked.snapshot.outputs[".pre-commit-config.yaml"])
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


def test_policy_lock_records_npm_s_lockfile_of_the_package_and_its_packages(
    tmp_path,
    releases,  # noqa: F811
):
    root = docs(tmp_path)
    assert authoring.lock(root).code == "locked"
    assert lock_records(root)["prettier-docs"] == {"versions": {PRETTIER: RECORD}}
    assert releases.resolved == [DEPENDENCIES]
    assert authoring.lock(root).changed == ()
    assert releases.resolved == [DEPENDENCIES]


def test_policy_add_resolves_the_packages_of_the_new_version(
    tmp_path,
    releases,  # noqa: F811
):
    root = locked_policy(tmp_path)
    releases.missing.add("prettier@3.10.2")
    missing = refused(authoring.add, root, "prettier-docs", "3.10.2")
    assert missing.code == "upstream_release_missing"
    assert str(missing) == (
        "npm cannot resolve the packages of prettier-docs 3.10.2: No matching "
        "version found for prettier@3.10.2."
    )
    assert missing.next_step == (
        "Check that prettier-docs 3.10.2 is released, then run gateseal policy "
        "add prettier-docs 3.10.2 again."
    )
    releases.missing.clear()
    assert authoring.add(root, "prettier-docs", "3.10.2").code == "added"
    assert releases.resolved[-1] == DEPENDENCIES | {"prettier": "3.10.2"}
    assert lock_records(root)["prettier-docs"]["versions"]["3.10.2"] == {
        "lockfile": lockfile(DEPENDENCIES | {"prettier": "3.10.2"})
    }


def test_a_package_npm_lacks_stops_the_lock(tmp_path, releases):  # noqa: F811
    releases.missing.add("prettier-plugin-toml@2.0.6")
    root = docs(tmp_path)
    failure = refused(authoring.lock, root)
    assert failure.code == "upstream_release_missing"
    assert "prettier-plugin-toml@2.0.6" in str(failure)
    assert failure.next_step == (
        f"Check that prettier-docs {PRETTIER} is released, or remove it from "
        "policy.toml. Then run gateseal policy lock."
    )
    assert not (root / "policy.lock").exists()


def test_a_package_bump_is_an_edit_and_a_lock(tmp_path, releases):  # noqa: F811
    root = locked_policy(tmp_path)
    (root / "policy.toml").write_text(DOCS.replace('"2.0.6"', '"2.0.7"'))
    stale = refused(load_policy_tree, root)
    assert (stale.code, stale.next_step) == ("policy_lock_stale", STALE_LOCK)
    bumped = DEPENDENCIES | {"prettier-plugin-toml": "2.0.7"}
    assert str(stale) == (
        f"policy.lock locks {json.dumps(DEPENDENCIES, sort_keys=True)} for "
        f"prettier-docs {PRETTIER}, but its definition and policy.toml give "
        f"{json.dumps(bumped, sort_keys=True)}."
    )
    assert authoring.lock(root).code == "locked"
    assert releases.resolved[-1] == bumped
    record = lock_records(root)["prettier-docs"]["versions"][PRETTIER]
    assert record == {"lockfile": lockfile(bumped)}
    assert authoring.check(root).code == "consistent"


@pytest.mark.parametrize("recorded", [1, {"lockfile": []}], ids=str)
def test_an_unreadable_record_is_resolved_again(tmp_path, releases, recorded):  # noqa: F811
    root = locked_policy(tmp_path)
    edit_record(root, lambda _: recorded)
    assert authoring.lock(root).code == "locked"
    assert lock_records(root)["prettier-docs"]["versions"][PRETTIER] == RECORD


def test_a_record_with_another_field_is_an_edited_lock(tmp_path, releases):  # noqa: F811
    root = locked_policy(tmp_path)
    edit_record(root, lambda record: record | {"integrity": "x"})
    failure = refused(load_policy_tree, root)
    assert failure.code == "policy_lock_edited"
    assert f"components.prettier-docs.versions.{PRETTIER} requires exactly" in (
        str(failure)
    )


@pytest.mark.parametrize("integrity", [None, "sha512-?", "md5-AAAA", "x sha512-AAAA"])
def test_a_package_without_an_integrity_hash_is_refused(
    tmp_path,
    releases,  # noqa: F811
    integrity,
):
    """A malformed hash is none: npm would install the package unchecked."""
    root = locked_policy(tmp_path)

    def unhashed(record):
        entry = record["lockfile"]["packages"]["node_modules/prettier-plugin-toml"]
        entry["integrity"] = integrity
        return record

    edit_record(root, unhashed)
    failure = refused(load_policy_tree, root)
    assert "policy.lock locks node_modules/prettier-plugin-toml for prettier-docs " in (
        str(failure)
    )
    assert str(failure).endswith("without an integrity hash.")


def test_a_bundled_package_needs_no_integrity_hash(tmp_path, releases):  # noqa: F811
    root = locked_policy(tmp_path)
    bundled = {"inBundle": True, "version": "1.0.0"}

    def bundle(record):
        packages = record["lockfile"]["packages"]
        packages["node_modules/prettier/node_modules/inner"] = bundled
        return record

    edit_record(root, bundle)
    assert authoring.check(root).code == "consistent"


def test_a_package_without_the_program_is_refused(tmp_path, releases):  # noqa: F811
    renamed = DEFINITION.replace('name = "prettier"\n\n', 'name = "prettierd"\n\n')
    root = docs(tmp_path, definition=renamed)
    failure = refused(authoring.lock, root)
    assert failure.code == "upstream_executable_missing"
    assert str(failure) == (
        f"The npm package prettier of prettier-docs {PRETTIER} links no program "
        "prettierd."
    )
    assert not (root / "policy.lock").exists()


def test_a_choice_key_cannot_load_prettier_plugins(tmp_path, releases):  # noqa: F811
    choice = (
        '\n[choices.plugin]\nkey = "plugins"\n\n[choices.plugin.since]\nx = "3.0.0"\n'
    )
    root = docs(tmp_path, definition=DEFINITION + choice)
    failure = refused(authoring.lock, root)
    assert failure.code == "invalid_field"
    assert str(failure) == (
        "tools/prettier-docs.toml: tools.prettier-docs.choices.plugin needs a "
        "lowercase name, a key other than extend and plugins, and a since table "
        "that maps lowercase values to stable releases."
    )


def test_a_choice_key_hides_no_override_with_plugins(tmp_path, releases):  # noqa: F811
    """A choice of the key overrides, which the policy need not expose."""
    choice = '\n[choices.over]\nkey = "overrides"\n[choices.over.since]\nx = "3.0.0"\n'
    override = '[[overrides]]\nfiles = ["*.md"]\n[overrides.options]\nplugins = ["x"]\n'
    root = docs(tmp_path, definition=DEFINITION + choice, rules=RULES + override)
    failure = refused(authoring.lock, root)
    assert str(failure).startswith(
        "The prettier-docs configuration must not name plugins"
    )


def test_the_tools_own_package_is_no_extra_package(tmp_path, releases):  # noqa: F811
    root = docs(tmp_path, DOCS.replace('"prettier-plugin-toml"', '"prettier"'))
    failure = refused(authoring.lock, root)
    assert failure.code == "duplicate_identifier"
    assert str(failure) == (
        "policy.toml pins prettier in the packages of prettier-docs, but prettier "
        "is the tool's own package, which its pin selects."
    )
    assert failure.next_step == "Remove prettier from the packages of prettier-docs."
    assert not (root / "policy.lock").exists()
    assert releases.resolved == []


def test_a_lock_naming_the_own_package_fails_at_load(tmp_path, releases):  # noqa: F811
    root = locked_policy(tmp_path)
    (root / "policy.toml").write_text(
        DOCS.replace('"prettier-plugin-toml"', '"prettier"')
    )
    assert refused(load_policy_tree, root).code == "duplicate_identifier"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            '"prettier-plugin-toml"',
            '"Prettier-Plugin"',
            "capabilities.docs.tools.prettier-docs.packages must be an npm package's",
        ),
        (
            '"2.0.6"',
            '"^2.0.6"',
            "capabilities.docs.tools.prettier-docs.packages.prettier-plugin-toml must "
            "be a stable major.minor.patch release.",
        ),
        (
            f"\n[capabilities.docs.tools.prettier-docs.packages]\n{PINS}",
            "packages = 1\n",
            "capabilities.docs.tools.prettier-docs.packages must be an object.",
        ),
    ],
    ids=["name", "version", "table"],
)
def test_each_package_is_a_name_at_an_exact_version(
    tmp_path,
    releases,  # noqa: F811
    old,
    new,
    message,
):
    assert old in DOCS
    failure = refused(authoring.lock, docs(tmp_path, DOCS.replace(old, new, 1)))
    assert failure.code == "invalid_field"
    assert str(failure).startswith(message)


def test_only_a_tool_on_node_takes_packages(tmp_path, upstream):  # noqa: F811
    policy = POLICY.replace(
        'reviewed = ["1.37.1"]\n', 'reviewed = ["1.37.1"]\npackages = {}\n'
    )
    failure = refused(authoring.lock, team(tmp_path, policy))
    assert failure.code == "invalid_field"
    assert str(failure) == (
        "capabilities.hygiene.tools.yamllint.packages needs a tool that runs on "
        "Node, but yamllint is a pre-commit-repository tool."
    )


def test_a_consumer_compiles_one_launch_on_the_node(tmp_path, releases):  # noqa: F811
    policy = load_policy_tree(locked_policy(tmp_path))
    locked, compiled = consumer(policy, ("hygiene", "docs"))
    tool = policy.tools["prettier-docs"]
    request = tool.node.prefix[3]
    entry = hooks(locked)["prettier-docs"]
    program = f"{shlex.join(tool.program()[5:])} {ARGS}"
    assert entry == {
        "id": "prettier-docs",
        "name": "prettier docs",
        "stages": ["pre-commit", "manual"],
        "types_or": ["markdown", "yaml"],
        "exclude": GENERATED,
        "language": "unsupported",
        "entry": f"mise --no-config exec {shlex.quote(request)} -- {program}",
        "require_serial": True,
    }
    assert "exclude" not in hooks(locked)["check-yaml"]
    assert compiled["components"]["prettier-docs"] == {
        "package": "prettier",
        "version": PRETTIER,
        "packages": PACKAGES,
        "hook_ids": ["prettier-docs"],
    }
    assert compiled["native_artifacts"]["node"]["version"] == VERSION
    outputs = locked.snapshot.outputs
    assert outputs[".gateseal/prettier-docs.toml"] == RULES.encode()
    lockfile_output = outputs[".gateseal/prettier-docs.package-lock.json"]
    assert lockfile_output == canonical(RECORD["lockfile"])
    copy = "cat .gateseal/prettier-docs.package-lock.json > "
    assert f'{copy}"$new/package-lock.json"' in entry["entry"]
    assert [note.message for note in describe(locked) if note.category == "tool"] == [
        "Tool pre-commit-hooks 6.0.0 for hygiene: built-in, pre-commit-repository.",
        f"Tool prettier-docs {PRETTIER} for docs: team-defined, npm-package on Node "
        f"{VERSION}" + CAVEAT,
    ]


def test_a_capability_exclude_follows_the_generated_files(tmp_path, releases):  # noqa: F811
    policy = load_policy_tree(locked_policy(tmp_path))
    choice = '\n[choices.docs]\nexclude = ["vendor", "docs/gen.d"]\n'
    locked, _ = consumer(policy, ("hygiene", "docs"), choice)
    assert hooks(locked)["prettier-docs"]["exclude"] == (
        GENERATED + r"|^(?:vendor(?:/|$)|docs/gen\.d(?:/|$))"
    )


def test_a_version_exception_changes_only_the_tools_version(
    tmp_path,
    releases,  # noqa: F811
):
    reviewed = DOCS.replace(
        f'reviewed = ["{PRETTIER}"]', f'reviewed = ["{PRETTIER}", "3.9.0"]'
    )
    policy = load_policy_tree(locked_policy(tmp_path, reviewed))
    pinned, _ = consumer(policy, ("hygiene", "docs"))
    exception = (
        '\n[exceptions.prettier-docs]\nversion = "3.9.0"\nreason = "r"\n'
        "review_by = 2099-12-31\n"
    )
    locked, compiled = consumer(policy, ("hygiene", "docs"), exception)
    path = ".gateseal/prettier-docs.package-lock.json"
    older = lockfile(DEPENDENCIES | {"prettier": "3.9.0"})
    assert locked.snapshot.outputs[path] == canonical(older)
    entry = hooks(locked)["prettier-docs"]["entry"]
    assert entry != hooks(pinned)["prettier-docs"]["entry"]
    assert compiled["components"]["prettier-docs"]["version"] == "3.9.0"
    assert compiled["components"]["prettier-docs"]["packages"] == PACKAGES


def test_offer_needs_the_node_and_the_executable(tmp_path, releases):  # noqa: F811
    root = locked_policy(tmp_path)
    catalog = review_policy(lambda name: (root / name).read_bytes()).catalogs
    tool = load_policy_tree(root).tools["prettier-docs"]
    nameless = replace(tool.definition, executable=None)
    for definition, node in ((tool.definition, None), (nameless, tool.node)):
        failure = refused(
            tool.source.offer,
            definition,
            tool.use,
            catalog["prettier-docs"],
            {PRETTIER: RECORD},
            node,
        )
        assert failure.code == "invalid_field"
        assert str(failure) == "tools.prettier-docs runs on Node with an executable."


@pytest.mark.parametrize(
    "package", ["prettier", "@biomejs/biome", "@typescript-eslint/eslint-plugin"]
)
def test_a_source_names_a_package_scoped_or_not(package):
    text = DEFINITION.replace('package = "prettier"', f'package = "{package}"')
    definition = load_definition("x", text.encode(), Origin.TEAM)
    assert definition.source.package == package


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('package = "prettier"', 'package = "prettier@3"'),
        ('package = "prettier"', 'package = "Prettier"'),
        ('package = "prettier"', 'package = "prettier"\nregistry = "x"'),
        ('package = "prettier"', 'name = "prettier"'),
        ('[executable]\nname = "prettier"\n', ""),
        ('name = "prettier"\n\n[configuration]', 'name = "bin/x"\n\n[configuration]'),
    ],
    ids=[
        "version",
        "uppercase",
        "unknown field",
        "no package",
        "no executable",
        "path",
    ],
)
def test_an_invalid_definition_names_its_field(old, new):
    assert old in DEFINITION
    text = DEFINITION.replace(old, new, 1)
    failure = refused(load_definition, "x", text.encode(), Origin.TEAM)
    assert failure.code == "invalid_field"
    assert "tools.x" in str(failure) or "npm-package tool" in str(failure)


def test_an_npm_package_tool_is_never_checked_at_commit():
    checked = DEFINITION.replace(
        'name = "prettier"\n\n', 'name = "prettier"\ncheck_at_commit = true\n\n'
    )
    assert checked != DEFINITION
    failure = refused(load_definition, "x", checked.encode(), Origin.TEAM)
    assert failure.code == "invalid_field"
    assert "a npm-package tool installs" in str(failure)


def test_every_reviewed_version_installs_the_same_packages(tmp_path, releases):  # noqa: F811
    reviewed = DOCS.replace(
        f'reviewed = ["{PRETTIER}"]', f'reviewed = ["{PRETTIER}", "3.9.0"]'
    )
    root = locked_policy(tmp_path, reviewed)
    assert lock_records(root)["prettier-docs"]["versions"] == {
        "3.9.0": {"lockfile": lockfile(DEPENDENCIES | {"prettier": "3.9.0"})},
        PRETTIER: RECORD,
    }


def test_a_skipped_optional_hook_leaves_the_tool_its_other_hooks(
    tmp_path,
    releases,  # noqa: F811
):
    check = (
        '\n[hooks.prettier-check]\nname = "prettier check"\n'
        'args = ["--check", "--"]\noutput = "plain"\n'
    )
    optional = '\n[capabilities.docs]\noptional = ["prettier-check"]\n'
    policy = DOCS.replace("[capabilities.docs.choices]\nexclude = []\n", "")
    policy += optional + "\n[capabilities.docs.choices]\nskip = []\n"
    root = locked_policy(tmp_path, policy, DEFINITION + check)
    policy = load_policy_tree(root)
    locked, compiled = consumer(policy, ("hygiene", "docs"))
    assert list(hooks(locked))[2:] == ["prettier-docs", "prettier-check"]
    skip = '\n[choices.docs]\nskip = ["prettier-check"]\n'
    locked, compiled = consumer(policy, ("hygiene", "docs"), skip)
    assert list(hooks(locked))[2:] == ["prettier-docs"]
    assert compiled["components"]["prettier-docs"]["hook_ids"] == ["prettier-docs"]


RUN = """\
check yaml...............................................................Passed
prettier docs............................................................Failed
- hook id: prettier-docs
- exit code: 1

README.md:3:1: prose-wrap Line is longer than 80 characters
"""


def timed(execute):
    """Return execute as the gate calls it, with no time and no timed line."""

    def execute_timed(arguments, root, env):
        return Timed(execute(arguments, root, env, True), 0.0, ())

    return execute_timed


def test_run_and_gate_name_the_node_of_each_hook_on_node(
    tmp_path,
    releases,  # noqa: F811
    monkeypatch,
):
    concise = DEFINITION.replace('output = "plain"', 'output = "concise"')
    locked, _ = consumer(
        load_policy_tree(locked_policy(tmp_path, definition=concise)),
        ("hygiene", "docs"),
    )

    def execute(command, root, env, allow_failure):
        return subprocess.CompletedProcess(command, 1, RUN.encode(), b"")

    monkeypatch.setattr(checks.setup, "require_pre_commit", lambda: "/python")
    monkeypatch.setattr(checks.fetch, "fetch", lambda locked, python: ())
    monkeypatch.setattr(checks, "execute", execute)
    monkeypatch.setattr(checks, "execute_timed", timed(execute))
    named = [
        ("hook", "check-yaml", None),
        ("hook", "prettier-docs", VERSION),
        ("finding", "prettier-docs", VERSION),
    ]
    run = checks.run(locked)
    assert [(e["category"], e["hook"], e.get("node")) for e in run.entries] == named
    gate = checks.gate(None, locked, Scope(None, None, None, "Checked every file."))
    found = [(e["category"], e["hook"], e.get("node")) for e in gate.entries[1:]]
    assert found == named


def test_a_history_finding_names_the_node_of_its_tool(
    tmp_path,
    releases,  # noqa: F811
    monkeypatch,
):
    scan = DEFINITION + '\n[history]\nargs = ["--check", "{range}"]\n'
    policy = DOCS.replace("[capabilities.docs.choices]\nexclude = []\n", "")
    root = locked_policy(tmp_path, policy, scan)
    locked, _ = consumer(load_policy_tree(root), ("hygiene", "docs"))
    found = f"notes.md:3:1: prose-wrap Too long in commit {'c' * 40}\n".encode()

    def execute(command, root, env, allow_failure):
        scanned = command[-1] == "base..head"
        output = found if scanned else b""
        return subprocess.CompletedProcess(command, int(scanned), output, b"")

    monkeypatch.setattr(checks.setup, "require_pre_commit", lambda: "/python")
    monkeypatch.setattr(checks.fetch, "fetch", lambda locked, python: ())
    monkeypatch.setattr(checks, "execute_timed", timed(execute))
    gate = checks.gate(None, locked, Scope(None, "base..head", None, "Checked."))
    [finding] = [e for e in gate.entries if e["category"] == "finding"]
    assert (finding["hook"], finding["file"]) == ("history", "notes.md")
    assert finding["node"] == VERSION


def warning(result):
    """Return the message of the one warning of result."""
    [found] = [e for e in result["diagnostics"] if e["severity"] == "warning"]
    assert found["category"] == "mise_auto_install_off"
    return found["message"]


def test_status_and_doctor_need_mise_for_a_tool_on_node(
    tmp_path,
    releases,  # noqa: F811
    hygiene,  # noqa: F811
    machine,  # noqa: F811
    monkeypatch,
):
    locked, compiled = consumer(
        load_policy_tree(locked_policy(tmp_path)), ("hygiene", "docs")
    )
    snapshot = replace(locked.snapshot, lock=canonical(compiled))
    monkeypatch.setattr(
        lock,
        "load",
        lambda root, *_, **__: replace(locked, root=root, snapshot=snapshot),
    )
    monkeypatch.setenv("MISE_AUTO_INSTALL", "0")
    code, result = gateseal(hygiene, "doctor")
    assert (code, result["code"]) == (3, "unhealthy")
    assert check(result, "mise") == ("error", "failed", f"{NEED} none is on PATH.")
    off = "mise's install at first use is off through MISE_AUTO_INSTALL, so "
    stops = "a hook on Node stops when mise has not installed the pinned Node"
    assert warning(result) == f"{off}{stops}."
    executable(machine / "mise", "#!/bin/sh\necho 2026.10.19 linux-x64\n")
    code, result = gateseal(hygiene, "status")
    assert (code, result["code"]) == (0, "healthy")
    assert check(result, "mise")[:2] == ("info", "passed")
