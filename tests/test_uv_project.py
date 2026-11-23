"""A policy runs a tool in the repository's uv project: the uv-project kind.

The team policy holds the mypy definition of docs/add-a-tool.md. A fake
upstream answers where gateseal policy lock and add would ask PyPI.
"""

import json
import shlex

import pytest
from gateseal import authoring, lock
from gateseal.lock import describe
from gateseal.policy import load_policy_tree
from gateseal.tools import load_definition
from gateseal.tools.model import Origin
from mypy_policy import DEFINITION, RELEASE, TABLE, VERSION
from test_lock_configure import configure, preparation, refused, tracked, worktree
from test_team_tools import (
    GITLEAKS_TABLE,
    GITLEAKS_TOOL,
    YAMLLINT,
    YAMLLINT_TABLE,
    consumer,
    hook_tool,
    team,
    upstream,  # noqa: F401
)
from test_team_tools import POLICY as TEAM_POLICY

POLICY = f"""\
schema = 5
release = "team-1"
minimum_compiler = "0.2.0"

[capabilities.hygiene.tools.pre-commit-hooks]
hook_ids = ["check-yaml"]
pin = "6.0.0"
reviewed = ["6.0.0"]

{TABLE}"""
OLDER = "2.4.0"
UV_RUN = [
    "env",
    "UV_PROJECT_ENVIRONMENT=.gateseal/environments/mypy",
    "uv",
    "run",
    "--locked",
    "--exact",
    "--all-packages",
]
ENTRY = [*UV_RUN, "--with", f"mypy=={VERSION}", "--", "mypy"]
ARGS = [
    "--strict",
    "--python-executable",
    ".gateseal/environments/mypy/bin/python",
    ".",
]
STALE_LOCK = "Run gateseal policy lock in the policy directory."


def typed(tmp_path, upstream, definition=DEFINITION, policy=POLICY):  # noqa: F811
    """Write the team policy with mypy, and let the fake PyPI hold its release."""
    upstream.files[RELEASE] = b"{}"
    files = {
        "rules/gitleaks.toml": None,
        "tools/yamllint.toml": None,
        "tools/gitleaks.toml": None,
        "tools/mypy.toml": definition,
    }
    return team(tmp_path, policy, files)


def hooks(locked):
    """Return the hooks of each repository in the generated configuration."""
    config = json.loads(locked.snapshot.outputs[".pre-commit-config.yaml"])
    return {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}


def test_policy_lock_records_the_package_once_pypi_has_the_release(
    tmp_path,
    upstream,  # noqa: F811
):
    root = typed(tmp_path, upstream)
    assert authoring.lock(root).code == "locked"
    components = json.loads((root / "policy.lock").read_bytes())["components"]
    assert components["mypy"] == {"versions": {VERSION: {"package": "mypy"}}}
    missing = refused(authoring.add, root, "mypy", OLDER)
    assert missing.code == "upstream_release_missing"
    assert str(missing) == f"PyPI has no release {OLDER} of mypy, the package of mypy."
    assert f"mypy {OLDER}" in missing.next_step
    upstream.files[RELEASE.replace(VERSION, OLDER)] = b"{}"
    assert authoring.add(root, "mypy", OLDER).code == "added"
    assert authoring.check(root).code == "consistent"
    assert sorted(load_policy_tree(root).tools["mypy"].versions) == [OLDER, VERSION]


def test_policy_lock_asks_pypi_for_the_package_without_its_extras(
    tmp_path,
    upstream,  # noqa: F811
):
    definition = DEFINITION.replace('"mypy"', '"mypy[faster-cache,reports]"', 1)
    root = typed(tmp_path, upstream, definition)
    authoring.lock(root)
    components = json.loads((root / "policy.lock").read_bytes())["components"]
    assert components["mypy"]["versions"][VERSION] == {
        "package": "mypy[faster-cache,reports]"
    }
    locked, _ = consumer(load_policy_tree(root), ("python-types",))
    entry = shlex.split(hooks(locked)["mypy"]["entry"])
    assert entry[entry.index("--with") + 1] == f"mypy[faster-cache,reports]=={VERSION}"


def test_a_policy_lock_for_another_package_is_stale(tmp_path, upstream):  # noqa: F811
    root = typed(tmp_path, upstream)
    authoring.lock(root)
    (root / "tools/mypy.toml").write_text(
        DEFINITION.replace('"mypy"', '"mypy-mirror"', 1)
    )
    stale = refused(load_policy_tree, root)
    assert (stale.code, stale.next_step) == ("policy_lock_stale", STALE_LOCK)
    assert str(stale) == (
        f"policy.lock records mypy {VERSION} for the package 'mypy', but its "
        "definition names mypy-mirror."
    )
    assert refused(authoring.check, root).code == "policy_lock_stale"
    before = (root / "policy.lock").read_bytes()
    changed = refused(authoring.lock, root)
    assert changed.code == "upstream_changed"
    assert str(changed) == (
        f'policy.lock records {{"package": "mypy"}} for mypy {VERSION}, but its '
        'definition now gives {"package": "mypy-mirror"}.'
    )
    assert (root / "policy.lock").read_bytes() == before


def test_a_consumer_compiles_the_uv_run_entry_and_labels_the_tool(
    tmp_path,
    upstream,  # noqa: F811
):
    root = typed(tmp_path, upstream)
    authoring.lock(root)
    locked, compiled = consumer(load_policy_tree(root), ("python-types",))
    assert hooks(locked)["mypy"] == {
        "id": "mypy",
        "name": "mypy",
        "stages": ["pre-commit", "manual"],
        "always_run": True,
        "pass_filenames": False,
        "language": "unsupported",
        "entry": shlex.join([*ENTRY, *ARGS]),
        "require_serial": True,
    }
    assert compiled["components"] == {
        "mypy": {"package": "mypy", "version": VERSION, "hook_ids": ["mypy"]}
    }
    assert compiled["native_artifacts"] == {}
    assert [note.message for note in describe(locked) if note.category == "tool"] == [
        f"Tool mypy {VERSION} for python-types: team-defined, uv-project; the "
        "repository's uv.lock pins what it imports; it carries the tool-specific "
        "guarantees only as far as its definition's arguments declare them."
    ]


def test_a_skipped_optional_hook_leaves_the_tool_its_other_hooks(
    tmp_path,
    upstream,  # noqa: F811
):
    tests_hook = (
        '\n[hooks.mypy-tests]\nname = "mypy tests"\nargs = ["--strict", "tests"]\n'
        'output = "plain"\n'
    )
    optional = (
        '\n[capabilities.python-types]\noptional = ["mypy-tests"]\n'
        "\n[capabilities.python-types.choices]\nskip = []\n"
    )
    root = typed(tmp_path, upstream, DEFINITION + tests_hook, POLICY + optional)
    authoring.lock(root)
    policy = load_policy_tree(root)
    locked, compiled = consumer(policy, ("python-types",))
    assert list(hooks(locked))[1:] == ["mypy", "mypy-tests"]
    skip = '\n[choices.python-types]\nskip = ["mypy-tests"]\n'
    locked, compiled = consumer(policy, ("python-types",), skip)
    assert list(hooks(locked))[1:] == ["mypy"]
    assert compiled["components"]["mypy"]["hook_ids"] == ["mypy"]


def test_a_version_exception_changes_the_version_in_the_entry(
    tmp_path,
    upstream,  # noqa: F811
):
    upstream.files[RELEASE.replace(VERSION, OLDER)] = b"{}"
    reviewed = POLICY.replace(
        f'reviewed = ["{VERSION}"]', f'reviewed = ["{VERSION}", "{OLDER}"]'
    )
    root = typed(tmp_path, upstream, policy=reviewed)
    authoring.lock(root)
    exception = (
        f'\n[exceptions.mypy]\nversion = "{OLDER}"\nreason = "r"\n'
        "review_by = 2099-12-31\n"
    )
    locked, compiled = consumer(load_policy_tree(root), ("python-types",), exception)
    assert shlex.split(hooks(locked)["mypy"]["entry"])[:11] == [
        *UV_RUN,
        "--with",
        f"mypy=={OLDER}",
        "--",
        "mypy",
    ]
    assert compiled["components"]["mypy"]["version"] == OLDER


@pytest.mark.parametrize(
    "package", ["mypy", "A", "ruamel.yaml", "pyright[nodejs]", "a-b_c[x,y.z]"]
)
def test_a_source_names_a_package_with_optional_extras(package):
    text = DEFINITION.replace('package = "mypy"', f'package = "{package}"')
    assert load_definition("mypy", text.encode(), Origin.TEAM).source.package == package


@pytest.mark.parametrize(
    "old,new",
    [
        ('package = "mypy"', 'package = "mypy==2.4.1"'),
        ('package = "mypy"', 'package = "../mypy"'),
        ('package = "mypy"', 'package = "-mypy"'),
        ('package = "mypy"', 'package = "mypy-"'),
        ('package = "mypy"', 'package = "mypy[]"'),
        ('package = "mypy"', 'package = "mypy[a,]"'),
        ('package = "mypy"', 'package = "mypy[a]b"'),
        ('package = "mypy"', 'package = "my py"'),
        ('package = "mypy"', 'package = ""'),
        ('package = "mypy"', 'package = "mypy"\nindex = "https://example.invalid"'),
        ('package = "mypy"', 'distribution = "mypy"'),
        ('[executable]\nname = "mypy"\n', ""),
        ('name = "mypy"\n\n[hooks', 'name = "../mypy"\n\n[hooks'),
    ],
    ids=[
        "version",
        "path",
        "leading hyphen",
        "trailing hyphen",
        "no extra",
        "empty extra",
        "text after extras",
        "space",
        "empty",
        "unknown field",
        "no package",
        "no executable",
        "executable path",
    ],
)
def test_an_invalid_definition_names_its_field(old, new):
    assert old in DEFINITION
    text = DEFINITION.replace(old, new, 1)
    failure = refused(load_definition, "mypy", text.encode(), Origin.TEAM)
    assert failure.code == "invalid_field"
    assert "tools.mypy" in str(failure) or "uv-project tool" in str(failure)


def test_a_hook_of_a_project_tool_may_list_file_types():
    text = DEFINITION.replace(
        'output = "plain"', 'types_or = ["python"]\noutput = "plain"'
    )
    assert text != DEFINITION
    definition = load_definition("mypy", text.encode(), Origin.TEAM)
    assert definition.hooks["mypy"].types_or == ("python",)


def test_a_uv_project_tool_takes_a_history_scan():
    text = DEFINITION + '\n[history]\nargs = ["--commits", "{range}"]\n'
    definition = load_definition("mypy", text.encode(), Origin.TEAM)
    assert definition.history == ("--commits", "{range}")


def test_a_uv_project_tool_is_never_checked_at_commit():
    checked = DEFINITION.replace(
        '[executable]\nname = "mypy"\n',
        '[executable]\nname = "mypy"\ncheck_at_commit = true\n',
    )
    assert checked != DEFINITION
    failure = refused(load_definition, "mypy", checked.encode(), Origin.TEAM)
    assert failure.code == "invalid_field"
    assert "tools.mypy.executable.check_at_commit" in str(failure)
    assert "a uv-project tool installs" in str(failure)


def unexcluded(tmp_path, scope):
    """Write the team policy of test_team_tools without exclude, tools at scope."""
    policy = TEAM_POLICY.replace("[capabilities.hygiene.choices]\nexclude = []\n", "")
    replaced = f'scope = "{scope}"'
    files = {
        "tools/yamllint.toml": hook_tool(YAMLLINT, "yamllint").replace(
            'scope = "files"', replaced
        ),
        "tools/gitleaks.toml": GITLEAKS_TOOL.replace('scope = "files"', replaced),
    }
    return team(tmp_path, policy, files)


@pytest.mark.parametrize(
    ("scope", "keys"),
    [("project", {"always_run": True, "pass_filenames": False}), ("files", {})],
)
def test_every_kind_runs_a_project_hook_always_and_without_file_names(
    tmp_path,
    upstream,  # noqa: F811
    scope,
    keys,
):
    root = unexcluded(tmp_path, scope)
    authoring.lock(root)
    locked, _ = consumer(load_policy_tree(root), ("hygiene", "secrets"))
    rendered = hooks(locked)
    for hook in ("yamllint", "gitleaks", "check-yaml"):
        found = {
            key: rendered[hook][key]
            for key in ("always_run", "pass_filenames")
            if key in rendered[hook]
        }
        assert found == ({} if hook == "check-yaml" else keys), hook


@pytest.mark.parametrize(
    ("tool", "table", "text"),
    [
        ("yamllint", YAMLLINT_TABLE, hook_tool(YAMLLINT, "yamllint")),
        ("gitleaks", GITLEAKS_TABLE, GITLEAKS_TOOL),
    ],
)
def test_a_capability_with_a_project_scope_tool_cannot_expose_exclude(
    tmp_path,
    upstream,  # noqa: F811
    tool,
    table,
    text,
):
    capability = "hygiene" if tool == "yamllint" else "secrets"
    policy = TEAM_POLICY.replace("[capabilities.hygiene.choices]\nexclude = []\n", "")
    policy += f"\n[capabilities.{capability}.choices]\nexclude = []\n"
    text = text.replace('scope = "files"', 'scope = "project"')
    if tool == "gitleaks":
        text = text.split("[history]")[0]
    root = team(tmp_path, policy, {f"tools/{tool}.toml": text})
    failure = refused(authoring.lock, root)
    assert failure.code == "invalid_field"
    assert str(failure) == (
        f"capabilities.{capability}.choices exposes exclude, but {tool} in "
        f"{capability} checks the whole project, which reads every path."
    )
    assert table.splitlines()[0] in policy


def project(root, *names):
    for name in names:
        (root / name).write_text("")
    return root


@pytest.mark.parametrize(
    ("present", "missing"),
    [
        ((), "pyproject.toml"),
        (("pyproject.toml",), "uv.lock"),
        (("uv.lock",), "pyproject.toml"),
    ],
)
def test_configure_needs_a_locked_uv_project_before_it_writes(
    tmp_path, present, missing
):
    root = project(worktree(tmp_path), *present)
    before = tracked(root)
    failure = refused(configure, root, ["hygiene", "python-types"])
    assert failure.code == "missing_project"
    assert str(failure) == (
        f"pyright runs in the repository's uv project, but the repository has no "
        f"{missing}. Only uv projects are supported."
    )
    assert failure.next_step == (
        f"Create the project's {missing} with uv and commit it, or stop selecting "
        "python-types in gateseal.toml. Then run gateseal configure."
    )
    assert tracked(root) == before


def test_a_pyproject_that_turns_uv_off_is_no_uv_project(tmp_path):
    root = project(worktree(tmp_path), "pyproject.toml", "uv.lock")
    (root / "pyproject.toml").write_text("[tool.uv]\nmanaged = false\n")
    before = tracked(root)
    failure = refused(configure, root, ["hygiene", "python-types"])
    assert failure.code == "missing_project"
    assert str(failure) == (
        "pyright runs in the repository's uv project, but pyproject.toml sets "
        "tool.uv.managed to false, so uv runs no project."
    )
    assert failure.next_step == (
        "Remove managed = false from [tool.uv], or stop selecting python-types in "
        "gateseal.toml. Then run gateseal configure."
    )
    assert tracked(root) == before


@pytest.mark.parametrize(
    "pyproject",
    ["[tool.uv]\nmanaged = true\n", "[tool.uv]\n", "tool = 1\n", "not TOML ["],
    ids=["managed", "unset", "no table", "not TOML"],
)
def test_configure_leaves_any_other_pyproject_to_uv(tmp_path, pyproject):
    root = project(worktree(tmp_path), "pyproject.toml", "uv.lock")
    (root / "pyproject.toml").write_text(pyproject)
    configure(root, ["hygiene", "python-types"])
    assert lock.load(root, preparation(root)).root == root


def test_a_folder_named_like_the_lockfile_is_no_uv_project(tmp_path):
    root = project(worktree(tmp_path), "pyproject.toml")
    (root / "uv.lock").mkdir()
    assert refused(configure, root, ["python-types"]).code == "missing_project"


def test_check_stops_once_the_project_files_are_gone(tmp_path):
    root = project(worktree(tmp_path), "pyproject.toml", "uv.lock")
    configure(root, ["hygiene", "python-types"])
    assert lock.load(root, preparation(root)).selection.tools[-1].version
    (root / "uv.lock").unlink()
    failure = refused(lock.load, root, preparation(root))
    assert failure.code == "missing_project"
    assert "has no uv.lock" in str(failure)


def test_a_selection_without_a_uv_project_tool_needs_no_project(tmp_path):
    root = worktree(tmp_path)
    configure(root, ["hygiene", "python"])
    assert lock.load(root, preparation(root)).root == root
