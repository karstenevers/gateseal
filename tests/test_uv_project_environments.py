"""Each uv-project tool runs in its own exact environment.

The hook names the tool's folder under .gateseal/environments and the
project's extras and dependency groups that the definition lists. The team
policy holds the mypy definition of docs/add-a-tool.md.
"""

import json
import shlex

import pytest
from conftest import BUNDLED_TOOLS
from gateseal import authoring
from gateseal.policy import load_bundled_policy, load_policy_tree
from gateseal.tools import load_definition
from gateseal.tools.model import Origin
from gateseal.tools.uv_project import UvProject
from mypy_policy import DEFINITION, VERSION
from test_lock_configure import refused
from test_team_tools import consumer, upstream  # noqa: F401
from test_uv_project import hooks, typed

SOURCE = 'package = "mypy"\n'
EXACT = ["uv", "run", "--locked", "--exact", "--all-packages"]
WITH_MYPY = ["--with", f"mypy=={VERSION}", "--", "mypy"]


def started(entry):
    """Return the words of entry up to the program it starts."""
    words = shlex.split(entry)
    return words[: words.index("--") + 2]


def test_pyright_runs_in_its_own_environment():
    locked, _ = consumer(load_bundled_policy(), ("python-types",))
    assert started(hooks(locked)["pyright"]["entry"]) == [
        "env",
        "UV_PROJECT_ENVIRONMENT=.gateseal/environments/pyright",
        *EXACT,
        "--with",
        f"pyright[nodejs]=={BUNDLED_TOOLS['pyright'].pin}",
        "--",
        "pyright",
    ]


@pytest.mark.parametrize(
    ("source", "options"),
    [
        ("", []),
        ("default_groups = true\n", []),
        ("default_groups = false\n", ["--no-default-groups"]),
        (
            'extras = ["http", "cli"]\ngroups = ["typing", "docs"]\n',
            [
                "--extra",
                "http",
                "--extra",
                "cli",
                "--group",
                "typing",
                "--group",
                "docs",
            ],
        ),
        (
            'groups = ["typing"]\ndefault_groups = false\nextras = ["http"]\n',
            ["--extra", "http", "--group", "typing", "--no-default-groups"],
        ),
    ],
    ids=["unset", "default groups", "no default groups", "in order", "all"],
)
def test_the_entry_installs_the_extras_and_groups_the_definition_names(
    tmp_path,
    upstream,  # noqa: F811
    source,
    options,
):
    root = typed(tmp_path, upstream, DEFINITION.replace(SOURCE, SOURCE + source))
    authoring.lock(root)
    components = json.loads((root / "policy.lock").read_bytes())["components"]
    assert components["mypy"] == {"versions": {VERSION: {"package": "mypy"}}}
    locked, compiled = consumer(load_policy_tree(root), ("python-types",))
    assert started(hooks(locked)["mypy"]["entry"]) == [
        "env",
        "UV_PROJECT_ENVIRONMENT=.gateseal/environments/mypy",
        *EXACT,
        *options,
        *WITH_MYPY,
    ]
    assert compiled["components"]["mypy"] == {
        "package": "mypy",
        "version": VERSION,
        "hook_ids": ["mypy"],
    }


def test_a_source_lists_the_project_s_extras_and_groups():
    text = DEFINITION.replace(
        SOURCE,
        SOURCE
        + 'extras = ["http", "a.b_c-d"]\ngroups = ["Typing"]\ndefault_groups = false\n',
    )
    source = load_definition("mypy", text.encode(), Origin.TEAM).source
    assert source == UvProject("mypy", ("http", "a.b_c-d"), ("Typing",), False)
    plain = load_definition("mypy", DEFINITION.encode(), Origin.TEAM).source
    assert plain == UvProject("mypy", (), (), True)


@pytest.mark.parametrize(
    ("line", "field", "message"),
    [
        ('extra = ["http"]', "source", "requires exactly"),
        ('extras = "http"', "source.extras", "must be an array"),
        ("groups = [1]", "source.groups", "must be a nonempty printable string"),
        ('groups = [""]', "source.groups", "must be a nonempty printable string"),
        ('extras = ["-http"]', "source.extras", "not '-http'"),
        ('groups = ["dev docs"]', "source.groups", "not 'dev docs'"),
        ('groups = ["dev-"]', "source.groups", "not 'dev-'"),
        ('extras = ["http", "cli", "http"]', "source.extras", "more than once"),
        ('groups = ["dev", "dev"]', "source.groups", "more than once"),
        ('default_groups = "false"', "source.default_groups", "true or false"),
        ("default_groups = 0", "source.default_groups", "true or false"),
    ],
    ids=[
        "unknown key",
        "not a list",
        "not a string",
        "empty",
        "leading hyphen",
        "space",
        "trailing hyphen",
        "repeated extra",
        "repeated group",
        "string",
        "number",
    ],
)
def test_an_invalid_extra_or_group_names_its_field(line, field, message):
    text = DEFINITION.replace(SOURCE, f"{SOURCE}{line}\n")
    failure = refused(load_definition, "mypy", text.encode(), Origin.TEAM)
    assert failure.code == "invalid_field"
    assert str(failure).startswith(f"tools.mypy.{field} ")
    assert message in str(failure)
