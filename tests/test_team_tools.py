"""A team's policy defines its capabilities and adds its own tools.

Each test writes a policy directory as its authors edit it, then runs gateseal
policy lock, add, pin and check, or what a consumer's configure and commit
compile from the policy. A fake upstream serves tags and release files from
dictionaries, where gateseal policy lock and add would reach GitHub.
"""

import hashlib
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from gateseal import authoring, checks, upgrade
from gateseal.ci import Scope
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError
from gateseal.environments import pre_commit_environment
from gateseal.hook_results import hook_results
from gateseal.lock import Locked, Snapshot, compile_lock, compile_outputs, describe
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import (
    bundled_root,
    content_stream,
    load_policy_stream,
    load_policy_tree,
)
from gateseal.tools import release_binary

HOOKS = "https://github.com/pre-commit/pre-commit-hooks"
RUFF = "https://github.com/astral-sh/ruff-pre-commit"
YAMLLINT = "https://github.com/adrienverge/yamllint"
GITLEAKS = "https://github.com/gitleaks/gitleaks"
CAVEAT = (
    "; it carries the tool-specific guarantees only as far as its definition's "
    "arguments declare them."
)
COMMIT = "c0ffee" + "0" * 34

POLICY = """\
schema = 4
release = "team-1"
minimum_compiler = "0.1.0"

[capabilities.hygiene]
tools = ["pre-commit-hooks", "yamllint"]

[capabilities.secrets]
tools = ["gitleaks"]

[tools.pre-commit-hooks]
hook_ids = ["check-yaml"]

[tools.yamllint]

[tools.gitleaks]
configuration = "rules/gitleaks.toml"

[choices.hygiene.exclude]
default = []

[components.pre-commit-hooks]
pin = "6.0.0"

[components.pre-commit-hooks.versions."6.0.0"]

[components.yamllint]
pin = "1.37.1"

[components.yamllint.versions."1.37.1"]

[components.gitleaks]
pin = "8.30.0"

[components.gitleaks.versions."8.30.0"]
"""

YAMLLINT_COMPONENT = (
    '[components.yamllint]\npin = "1.37.1"\n\n[components.yamllint.versions."1.37.1"]\n'
)


def hook_tool(repository, hook, extra=""):
    """Return a pre-commit-repository definition with one plain hook."""
    return f"""\
kind = "pre-commit-repository"
stages = ["commit", "run", "ci"]
scope = "files"
removed_prefixes = ["{hook.upper()}_"]
{extra}
[source]
repository = "{repository}"
tag = "v{{version}}"

[hooks.{hook}]
name = "{hook} check"
args = ["--strict"]
probe = ["{hook}", "--version"]
output = "plain"
"""


GITLEAKS_TOOL = """\
kind = "release-binary"
stages = ["commit", "run", "ci"]
scope = "files"
removed_prefixes = ["GITLEAKS_"]

[source]
repository = "https://github.com/gitleaks/gitleaks"
tag = "v{version}"
archive = "gitleaks_{version}_{platform}.tar.gz"
checksums = "gitleaks_{version}_checksums.txt"

[source.platforms]
linux-x64 = "linux_x64"
macos-arm64 = "darwin_arm64"

[configuration]
format = "toml"

[executable]
name = "gitleaks"
version = ["version"]
version_output = "{version}"

[files.report]
extension = "tmpl"
content = '''
{{ range . }}{{ .File }}:{{ .StartLine }}:{{ .StartColumn }}: {{ .RuleID }}
{{ end }}'''

[hooks.gitleaks]
name = "Detect secrets with Gitleaks"
args = ["dir", "--config", "{configuration}", "--redact", "--report-path", "-"]
probe = ["gitleaks", "version"]
output = "concise"

[history]
args = ["git", "--config", "{configuration}", "--redact", "--log-opts={range}"]
"""

GITLEAKS_RULES = 'title = "Team secret rules"\n'


class Upstream:
    """Tags and release files as gateseal policy lock reads them."""

    def __init__(self):
        lock = json.loads((bundled_root() / "policy.lock").read_bytes())
        hooks = lock["components"]["pre-commit-hooks"]["versions"]
        self.tags = {(HOOKS, f"v{v}"): r["revision"] for v, r in hooks.items()}
        self.tags[YAMLLINT, "v1.37.1"] = COMMIT
        self.files = {}
        self.release("8.30.0")

    def release(self, version):
        base = f"{GITLEAKS}/releases/download/v{version}/"
        lines = []
        for platform in ("linux_x64", "darwin_arm64"):
            name = f"gitleaks_{version}_{platform}.tar.gz"
            self.files[base + name] = name.encode()
            lines.append(f"{hashlib.sha256(name.encode()).hexdigest()}  {name}")
        self.files[base + f"gitleaks_{version}_checksums.txt"] = "\n".join(
            lines
        ).encode()

    def tagged(self, repository, tag):
        return self.tags.get((repository, tag))

    def download(self, url):
        return self.files.get(url)


@pytest.fixture
def upstream(monkeypatch):
    fake = Upstream()
    monkeypatch.setattr(authoring, "Releases", lambda git, scratch, rerun: fake)
    return fake


def team(tmp_path, policy=POLICY, files=None):
    """Write a team policy, with files replacing its files by name or None."""
    root = tmp_path / "policy"
    written = {
        "policy.toml": policy,
        "rules/gitleaks.toml": GITLEAKS_RULES,
        "tools/yamllint.toml": hook_tool(YAMLLINT, "yamllint"),
        "tools/gitleaks.toml": GITLEAKS_TOOL,
    } | (files or {})
    for name, text in written.items():
        if text is not None:
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
    return root


def consumer(policy, capabilities, extra=""):
    """Return what configure compiles for capabilities: selection and Lock."""
    source = BundledSource(policy.release)
    manifest = render(capabilities, source) + extra.encode()
    wheel = "gateseal-0.1.0-py3-none-any.whl"
    artifact = CompilerArtifact("local-wheel", f"/{wheel}", wheel, "a" * 64, "0.1.0")
    selection = resolve(parse_manifest(manifest), policy)
    lock = compile_lock(manifest, source, policy, artifact, selection)
    outputs = compile_outputs(selection, artifact, source)
    snapshot = Snapshot(manifest, b"", outputs)
    return Locked(Path(), policy, artifact, snapshot, source, selection), lock


def test_team_tools_lock_add_pin_and_check_like_built_in_tools(tmp_path, upstream):
    root = team(tmp_path)
    locked = authoring.lock(root)
    assert (locked.code, locked.changed) == ("locked", ("policy.lock",))
    components = json.loads((root / "policy.lock").read_bytes())["components"]
    assert components["yamllint"] == {"versions": {"1.37.1": {"revision": COMMIT}}}
    archive = "gitleaks_8.30.0_darwin_arm64.tar.gz"
    assert components["gitleaks"]["versions"]["8.30.0"]["platforms"]["macos-arm64"] == {
        "executable": "gitleaks",
        "sha256": hashlib.sha256(archive.encode()).hexdigest(),
        "url": f"{GITLEAKS}/releases/download/v8.30.0/{archive}",
    }
    upstream.release("8.31.0")
    added = authoring.add(root, "gitleaks", "8.31.0")
    assert (added.code, added.changed) == ("added", ("policy.toml", "policy.lock"))
    assert "gateseal policy pin gitleaks 8.31.0" in added.next_steps[0]
    assert authoring.pin(root, "gitleaks", "8.31.0").code == "pinned"
    upstream.tags[YAMLLINT, "v1.38.0"] = "f" * 40
    assert authoring.add(root, "yamllint", "1.38.0").code == "added"
    assert authoring.check(root).code == "consistent"
    policy = load_policy_tree(root)
    assert {name: (t.pin, sorted(t.versions)) for name, t in policy.tools.items()} == {
        "pre-commit-hooks": ("6.0.0", ["6.0.0"]),
        "yamllint": ("1.37.1", ["1.37.1", "1.38.0"]),
        "gitleaks": ("8.31.0", ["8.30.0", "8.31.0"]),
    }
    assert {"tools/gitleaks.toml", "tools/yamllint.toml"} <= {
        asset.path for asset in policy.assets
    }
    assert load_policy_stream(content_stream(policy.assets)) == policy


def test_a_consumer_compiles_and_labels_team_tools(tmp_path, upstream):
    root = team(tmp_path)
    authoring.lock(root)
    policy = load_policy_tree(root)
    locked, lock = consumer(policy, ("hygiene", "secrets"))
    assert lock["components"]["yamllint"] == {
        "repository": YAMLLINT,
        "version": "1.37.1",
        "revision": COMMIT,
        "hook_ids": ["yamllint"],
        "tools": {},
    }
    assert set(lock["components"]) == {"pre-commit-hooks", "yamllint"}
    assert set(lock["native_artifacts"]) == {"gitleaks"}
    outputs = locked.snapshot.outputs
    config = json.loads(outputs[".pre-commit-config.yaml"])
    assert [repository["repo"] for repository in config["repos"]] == [
        HOOKS,
        YAMLLINT,
        "local",
    ]
    assert outputs[".gateseal/gitleaks.toml"] == GITLEAKS_RULES.encode()
    assert ".gateseal/gitleaks-report.tmpl" in outputs
    assert [note.message for note in describe(locked) if note.category == "tool"] == [
        "Tool pre-commit-hooks 6.0.0 for hygiene: built-in, pre-commit-repository.",
        "Tool yamllint 1.37.1 for hygiene: team-defined, pre-commit-repository"
        + CAVEAT,
        "Tool gitleaks 8.30.0 for secrets: team-defined, release-binary" + CAVEAT,
    ]


RUN = """\
check yaml...............................................................Passed
yamllint check...........................................................Failed
- hook id: yamllint
- exit code: 1

config.yaml:1:1: [error] syntax error (syntax)
Detect secrets with Gitleaks.............................................Failed
- hook id: gitleaks
- exit code: 1

keys.env:2:7: team-token (A team token)
"""


def test_only_a_concise_team_hook_reports_findings(tmp_path, upstream):
    root = team(tmp_path)
    authoring.lock(root)
    locked, _ = consumer(load_policy_tree(root), ("hygiene", "secrets"))
    results = hook_results(RUN, locked.selection.hooks, "run")
    assert [
        (r["category"], r["tool"], r["origin"], r.get("status"), r.get("file"))
        for r in results
    ] == [
        ("hook", "pre-commit-hooks", "built-in", "passed", None),
        ("hook", "yamllint", "team-defined", "failed", None),
        ("hook", "gitleaks", "team-defined", "failed", None),
        ("finding", "gitleaks", "team-defined", None, "keys.env"),
    ]
    assert results[-1]["rule"] == "team-token"


def test_a_team_history_scan_names_its_origin(tmp_path, upstream, monkeypatch):
    root = team(tmp_path)
    authoring.lock(root)
    locked, _ = consumer(load_policy_tree(root), ("hygiene", "secrets"))
    prepared = Path("/attempt")
    paths = SimpleNamespace(
        pre_commit_python=prepared / "pre-commit/bin/python",
        pre_commit_home=prepared / "pre-commit-home",
        tools=prepared / "tools",
        git=Path("/usr/bin/git"),
    )

    @contextmanager
    def pinned(worktree, pinned_lock):
        yield SimpleNamespace(paths=paths)

    def execute(command, root, env, allow_failure):
        if command[0] == str(paths.tools / "gateseal-gitleaks"):
            line = f"leak.env:3:1: team-token (A team token) in commit {'a' * 40}\n"
            return subprocess.CompletedProcess(command, 1, line.encode(), b"")
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(checks.runtime, "pinned", pinned)
    monkeypatch.setattr(checks, "execute", execute)
    result = checks.gate(None, locked, Scope(None, "a..b", None, "Scanned."))
    assert result.code == "checks_failed"
    assert [
        (entry["category"], entry["tool"], entry["origin"], entry.get("file"))
        for entry in result.entries
    ] == [
        ("history", "gitleaks", "team-defined", None),
        ("finding", "gitleaks", "team-defined", "leak.env"),
    ]


@pytest.mark.parametrize(
    "old,new,field",
    [
        ('kind = "release-binary"', 'kind = "node-package"', "kind"),
        ('"run", "ci"]', '"run", "ci", "push"]', "stages"),
        ('scope = "files"', 'scope = "project"', "scope"),
        ('output = "concise"', 'output = "sarif"', "hooks.gitleaks.output"),
        ('format = "toml"', 'format = "yaml"', "configuration.format"),
    ],
)
def test_an_unknown_value_in_a_team_definition_fails_before_any_write(
    tmp_path, upstream, old, new, field
):
    assert old in GITLEAKS_TOOL
    root = team(
        tmp_path, files={"tools/gitleaks.toml": GITLEAKS_TOOL.replace(old, new)}
    )
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "unsupported_tool_definition"
    assert str(failure.value).startswith(f"tools/gitleaks.toml: tools.gitleaks.{field}")
    assert "Raise minimum_compiler in policy.toml" in failure.value.next_step
    assert not (root / "policy.lock").exists()


def test_a_built_in_name_is_reserved(tmp_path, upstream):
    copy = (bundled_root().parent / "tools/pre-commit-hooks.tool.toml").read_text()
    root = team(tmp_path, files={"tools/pre-commit-hooks.toml": copy})
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "reserved_tool_name"
    assert "copy its definition to tools/<new name>.toml" in failure.value.next_step
    assert not (root / "policy.lock").exists()


@pytest.mark.parametrize("name", ["pre-commit", "python"])
def test_the_compiler_runtimes_names_are_reserved(tmp_path, upstream, name):
    policy = POLICY.replace('"yamllint"', f'"{name}"').replace(
        "[tools.yamllint]", f"[tools.{name}]"
    )
    root = team(tmp_path, policy, {f"tools/{name}.toml": hook_tool(YAMLLINT, "x")})
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "reserved_tool_name"
    assert "name the Compiler Runtime" in str(failure.value)


@pytest.mark.parametrize("prefix", ["GIT_", "GIT_INDEX_"])
def test_a_team_tool_cannot_drop_the_index_a_commit_names(tmp_path, upstream, prefix):
    definition = GITLEAKS_TOOL.replace('["GITLEAKS_"]', f'["GITLEAKS_", "{prefix}"]')
    root = team(tmp_path, files={"tools/gitleaks.toml": definition})
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "invalid_field"
    assert "none may remove GIT_DIR or GIT_INDEX_FILE" in str(failure.value)
    assert not (root / "policy.lock").exists()


def test_a_tool_neither_gateseal_nor_the_policy_defines_is_unknown(tmp_path):
    root = team(tmp_path, files={"tools/yamllint.toml": None})
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "unknown_tool"
    assert str(failure.value) == (
        "The policy names the tool yamllint, but gateseal has no built-in tool "
        "yamllint and the policy has no tools/yamllint.toml."
    )


@pytest.mark.parametrize(
    "old,new,claim",
    [
        (YAMLLINT, HOOKS, "hook repository"),
        ("[hooks.yamllint]", "[hooks.check-yaml]", "hook ID"),
        ('name = "yamllint check"', 'name = "check yaml"', "hook name"),
    ],
)
def test_a_policys_tools_claim_distinct_hooks_and_repositories(
    tmp_path, old, new, claim
):
    definition = hook_tool(YAMLLINT, "yamllint")
    assert old in definition
    root = team(tmp_path, files={"tools/yamllint.toml": definition.replace(old, new)})
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "duplicate_identifier"
    assert f"pre-commit-hooks and yamllint both claim the {claim}" in str(failure.value)


@pytest.mark.parametrize(
    "old,new,code",
    [
        ("[capabilities.hygiene]", "[capabilities.tidy]", "invalid_field"),
        (
            'tools = ["gitleaks"]',
            'tools = ["gitleaks", "yamllint"]',
            "duplicate_identifier",
        ),
        (
            'tools = ["gitleaks"]',
            'tools = ["gitleaks", "gitleaks"]',
            "duplicate_identifier",
        ),
        ('tools = ["gitleaks"]', "tools = []", "invalid_field"),
        (
            "[tools.yamllint]\n",
            "[tools.yamllint]\n\n[tools.semgrep]\n",
            "invalid_field",
        ),
        ("[tools.yamllint]\n", "", "invalid_field"),
        (
            "[tools.yamllint]\n",
            '[tools.yamllint]\nhook_ids = ["ruff"]\n',
            "unsupported_hook",
        ),
        (YAMLLINT_COMPONENT, "", "unsupported_component"),
        ('hook_ids = ["check-yaml"]', 'hook_ids = ["yamllint"]', "unsupported_hook"),
    ],
    ids=[
        "no hygiene",
        "tool in two capabilities",
        "tool twice",
        "no tools",
        "tools table without a capability",
        "tool without a table",
        "hook of another tool",
        "components that differ from the tools",
        "hook of another tool in hygiene",
    ],
)
def test_policy_schema_4_structure_is_validated(tmp_path, upstream, old, new, code):
    assert old in POLICY
    root = team(tmp_path, POLICY.replace(old, new, 1))
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == code
    assert not (root / "policy.lock").exists()


def test_omitted_hook_ids_run_every_hook_in_definition_order(tmp_path, upstream):
    root = team(tmp_path, POLICY.replace('hook_ids = ["check-yaml"]\n', ""))
    authoring.lock(root)
    hooks = load_policy_tree(root).tools["pre-commit-hooks"].use.hooks
    assert [hook.id for hook in hooks] == [
        "trailing-whitespace",
        "end-of-file-fixer",
        "check-yaml",
    ]


PYTHON = """
[capabilities.python]
tools = ["strict-ruff"]

[tools.strict-ruff]
hook_ids = ["ruff-check"]
configuration = "rules/ruff.toml"

[choices.python.target_version]
default = "py311"

[components.strict-ruff]
pin = "0.17.1"

[components.strict-ruff.versions."0.17.1"]
"""


def python_policy(tmp_path, extra=""):
    ruff = (bundled_root().parent / "tools/ruff.tool.toml").read_text()
    rules = (bundled_root() / "rules/ruff.toml").read_text()
    policy = POLICY.replace(
        "\n[tools.pre-commit-hooks]", PYTHON + extra + "\n[tools.pre-commit-hooks]", 1
    )
    files = {"tools/strict-ruff.toml": ruff, "rules/ruff.toml": rules}
    return team(tmp_path, policy, files)


def test_a_team_definition_choice_renders_into_its_configuration(tmp_path, upstream):
    upstream.tags[RUFF, "v0.17.1"] = "e" * 40
    root = python_policy(tmp_path)
    authoring.lock(root)
    policy = load_policy_tree(root)
    choice = '\n[choices.python]\ntarget_version = "py39"\n'
    locked, lock = consumer(policy, ("hygiene", "python"), choice)
    assert lock["choices"]["python"] == {"target_version": "py39"}
    assert locked.snapshot.outputs[".gateseal/strict-ruff.toml"] == (
        b'target-version = "py39"\n' + (root / "rules/ruff.toml").read_bytes()
    )
    assert policy.tools["strict-ruff"].definition.origin == "team-defined"


def test_two_tools_of_a_capability_cannot_declare_one_choice(tmp_path, upstream):
    other = hook_tool(
        "https://github.com/team/pyfmt",
        "pyfmt",
        '[configuration]\nformat = "toml"\n\n[choices.target_version]\n'
        'key = "target"\nsince = { py311 = "1.0.0" }\n',
    )
    extra = (
        '\n[tools.pyfmt]\nconfiguration = "rules/pyfmt.toml"\n\n'
        '[components.pyfmt]\npin = "1.0.0"\n\n[components.pyfmt.versions."1.0.0"]\n'
    )
    root = python_policy(tmp_path, extra)
    policy = root / "policy.toml"
    policy.write_text(
        policy.read_text().replace('["strict-ruff"]', '["strict-ruff", "pyfmt"]')
    )
    (root / "tools/pyfmt.toml").write_text(other)
    (root / "rules/pyfmt.toml").write_text("")
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "invalid_field"
    assert "declares the choice target_version" in str(failure.value)


@pytest.mark.parametrize(
    "component,code",
    [("gitleaks", "unused_exception"), ("ruff", "unsupported_component")],
)
def test_an_exception_names_a_tool_the_policy_offers(
    tmp_path, upstream, component, code
):
    root = team(tmp_path)
    authoring.lock(root)
    exception = (
        f'\n[exceptions.{component}]\nversion = "1.0.0"\nreason = "r"\n'
        "review_by = 2026-12-31\n"
    )
    with pytest.raises(GatesealError) as failure:
        consumer(load_policy_tree(root), ("hygiene",), exception)
    assert failure.value.code == code


def test_processes_drop_the_settings_of_the_selected_team_tools(
    tmp_path, upstream, monkeypatch
):
    root = team(tmp_path)
    authoring.lock(root)
    locked, _ = consumer(load_policy_tree(root), ("hygiene", "secrets"))
    for key in ("YAMLLINT_CONFIG_FILE", "RUFF_CONFIG", "TEAM_SETTING"):
        monkeypatch.setenv(key, "x")
    paths = SimpleNamespace(
        pre_commit_home=Path("/home"),
        tools=Path("/tools"),
        pre_commit_python=Path("/python/bin/python"),
        git=Path("/usr/bin/git"),
    )
    environment = pre_commit_environment(paths, locked.selection.tools)
    assert "TEAM_SETTING" in environment
    assert not {"YAMLLINT_CONFIG_FILE", "RUFF_CONFIG"} & set(environment)


def test_upgrade_targets_a_tool_that_the_new_release_adds(tmp_path, upstream):
    first = (
        POLICY.replace('"pre-commit-hooks", "yamllint"', '"pre-commit-hooks"')
        .replace("[tools.yamllint]\n\n", "")
        .replace(YAMLLINT_COMPONENT, "")
    )
    old, new = team(tmp_path / "v1", first), team(tmp_path / "v2")
    authoring.lock(old)
    authoring.lock(new)
    current, _ = consumer(load_policy_tree(old), ("hygiene",))
    request = upgrade.Request(None, None, {"yamllint": None}, None, None, False)
    assert upgrade._exception_changes(
        parse_manifest(current.snapshot.manifest),
        current,
        load_policy_tree(new),
        request,
    ) == ({}, frozenset())


def test_upgrade_names_each_lock_record_by_its_tool():
    lock = {
        "components": {
            "pre-commit-hooks": {"repository": HOOKS, "version": "6.0.0"},
            "hygiene": {"repository": HOOKS, "version": "5.0.0"},
        },
        "native_artifacts": {"gitleaks": {"version": "8.30.0"}},
    }
    assert upgrade._versions(lock) == {
        "pre-commit-hooks": "6.0.0",
        "hygiene": "5.0.0",
        "gitleaks": "8.30.0",
    }


def test_a_release_binary_probe_runs_on_empty_input(tmp_path, upstream, monkeypatch):
    root = team(tmp_path)
    authoring.lock(root)
    locked, _ = consumer(load_policy_tree(root), ("hygiene", "secrets"))
    given = []

    def execute(arguments, root, env, **options):
        given.append((arguments[1:], options.get("stdin")))
        return SimpleNamespace(stdout=b"8.30.0\n")

    monkeypatch.setattr(release_binary, "execute", execute)
    (gitleaks,) = (t for t in locked.selection.tools if t.definition.name == "gitleaks")
    release_binary._probe(gitleaks, "8.30.0", Path("/gateseal-gitleaks"), Path(), {})
    assert given == [(["version"], b""), (["version"], b"")]


def test_a_team_choice_cannot_make_its_configuration_load_another(tmp_path, upstream):
    choice = '[choices.base]\nkey = "extend"\n\n[choices.base.since]\nx = "8.0.0"\n'
    root = team(tmp_path, files={"tools/gitleaks.toml": GITLEAKS_TOOL + choice})
    with pytest.raises(GatesealError) as failure:
        authoring.lock(root)
    assert failure.value.code == "invalid_field"
    assert "a key other than extend" in str(failure.value)
    assert not (root / "policy.lock").exists()
