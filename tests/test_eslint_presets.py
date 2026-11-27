"""An ESLint rule file names presets, whose rules the Policy Lock records.

A team policy runs the built-in ESLint with typescript-eslint. gateseal
policy lock reads each preset once from the fake upstream of node_policy,
which answers as the program on the policy's Node would, and every other
command reads the recorded rules offline.
"""

import json

import pytest
from gateseal import authoring
from gateseal.policy import load_policy_tree
from gateseal.validation import canonical
from node_policy import VERSION, docs, lockfile, releases  # noqa: F401
from test_lock_configure import refused
from test_team_tools import consumer, team

ESLINT = "10.15.0"
PLUGIN = "@typescript-eslint/eslint-plugin"
PACKAGES = {PLUGIN: "8.73.0", "@typescript-eslint/parser": "8.73.0"}
DEPENDENCIES = {"eslint": ESLINT, **PACKAGES}
STALE_LOCK = "Run gateseal policy lock in the policy directory."

POLICY = f"""\
schema = 5
release = "team-1"
minimum_compiler = "0.2.0"
node = "{VERSION}"

[capabilities.typescript.tools.eslint]
configuration = "rules/eslint.toml"
pin = "{ESLINT}"
reviewed = ["{ESLINT}"]

[capabilities.typescript.tools.eslint.packages]
"{PLUGIN}" = "8.73.0"
"@typescript-eslint/parser" = "8.73.0"
"""

RULES = """\
files = ["**/*.ts"]
parser = "@typescript-eslint/parser"
presets = ["eslint/recommended", "@typescript-eslint/recommended"]

[plugins]
"@typescript-eslint" = "@typescript-eslint/eslint-plugin"

[rules]
no-var = "off"
"""

CORE = [{"rules": {"no-undef": "error", "no-var": "error"}}]
TYPESCRIPT = [
    {"files": ["**/*.ts", "**/*.mts"], "rules": {"no-undef": "off"}},
    {"rules": {"@typescript-eslint/no-explicit-any": ["error", {"fixToUnknown": 0}]}},
]
STRICT = [{"rules": {"@typescript-eslint/no-namespace": "warn"}}]
QUIET = {"rules": {"react/jsx-key": "off", "react/no-danger": ["off", {"x": 1}]}}

PRESETS = {
    "eslint": {"recommended": {"blocks": CORE, "typed": []}},
    PLUGIN: {
        "recommended": {"blocks": TYPESCRIPT, "typed": []},
        "strict": {"blocks": STRICT, "typed": []},
        "recommended-type-checked": {
            "blocks": [{"rules": {"@typescript-eslint/await-thenable": "error"}}],
            "typed": ["@typescript-eslint/await-thenable"],
        },
        "legacy": {"blocks": [{"extends": ["base"], "rules": {}}], "typed": []},
        "functions": {"blocks": [{"files": [None], "rules": {}}], "typed": []},
        "react": {"blocks": [{"rules": {"react/jsx-key": "error"}}], "typed": []},
        "quiet-react": {"blocks": [QUIET], "typed": []},
    },
    "@typescript-eslint/parser": {"recommended": {"blocks": STRICT, "typed": []}},
}

RECORD = {
    "lockfile": lockfile(DEPENDENCIES),
    "presets": {"eslint": {"recommended": CORE}, PLUGIN: {"recommended": TYPESCRIPT}},
}

NOTE = (
    "The eslint configuration takes only the rules of its presets. Its files, "
    "parser and plugins apply; a preset's other settings, such as parser "
    "options, do not."
)


@pytest.fixture
def preset_releases(releases):  # noqa: F811
    """The fake upstream, whose program reads PRESETS."""
    releases.presets = PRESETS
    return releases


def eslint_policy(tmp_path, rules=RULES, policy=POLICY):
    files = {
        "rules/gitleaks.toml": None,
        "tools/yamllint.toml": None,
        "tools/gitleaks.toml": None,
        "rules/eslint.toml": rules,
    }
    return team(tmp_path, policy, files)


def record(root, version=ESLINT):
    lock = json.loads((root / "policy.lock").read_bytes())
    return lock["components"]["eslint"]["versions"][version]


def edit_presets(root, edit):
    """Rewrite the presets of eslint's Policy Lock record with edit."""
    lock = json.loads((root / "policy.lock").read_bytes())
    versions = lock["components"]["eslint"]["versions"]
    versions[ESLINT] = edit(versions[ESLINT])
    (root / "policy.lock").write_bytes(canonical(lock))


def asked(preset_releases):
    """Return each preset that the program read, by package, in order."""
    return [argument["presets"] for _, _, _, argument in preset_releases.evaluated]


def test_lock_records_each_preset_s_rules_beside_the_lockfile(
    tmp_path, preset_releases
):
    root = eslint_policy(tmp_path)
    locked = authoring.lock(root)
    assert locked.code == "locked"
    assert [note.message for note in locked.notes][1:] == [NOTE]
    assert record(root) == RECORD
    assert preset_releases.evaluated == [
        (
            "eslint_presets.mjs",
            "eslint",
            ESLINT,
            {
                "plugins": {"@typescript-eslint": PLUGIN},
                "presets": {"eslint": ["recommended"], PLUGIN: ["recommended"]},
            },
        )
    ]


def test_configure_writes_the_recorded_rules_as_layers_without_a_preset_name(
    tmp_path,
    preset_releases,
):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    locked, _ = consumer(load_policy_tree(root), ["typescript"])
    data = locked.snapshot.outputs[".gateseal/eslint.json"]
    assert b"recommended" not in data
    assert json.loads(data)["layers"] == [
        {"files": ["**/*.ts"], "rules": CORE[0]["rules"]},
        {
            "files": [["**/*.ts", "**/*.ts"], ["**/*.ts", "**/*.mts"]],
            "rules": {"no-undef": "off"},
        },
        {"files": ["**/*.ts"], "rules": TYPESCRIPT[1]["rules"]},
        {"files": ["**/*.ts"], "rules": {"no-var": "off"}},
    ]


def test_a_rerun_with_nothing_changed_asks_neither_npm_nor_node(
    tmp_path, preset_releases
):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    before = (root / "policy.lock").read_bytes()
    assert authoring.lock(root).changed == ()
    assert (len(preset_releases.resolved), len(preset_releases.evaluated)) == (1, 1)
    assert (root / "policy.lock").read_bytes() == before


def test_a_new_preset_is_read_alone_and_a_removed_one_is_dropped(
    tmp_path, preset_releases
):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    added = RULES.replace(
        '"@typescript-eslint/recommended"]',
        '"@typescript-eslint/recommended", "@typescript-eslint/strict"]',
    )
    (root / "rules/eslint.toml").write_text(added)
    authoring.lock(root)
    assert asked(preset_releases)[1:] == [{PLUGIN: ["strict"]}]
    assert record(root)["presets"][PLUGIN] == {
        "recommended": TYPESCRIPT,
        "strict": STRICT,
    }
    (root / "rules/eslint.toml").write_text(
        RULES.replace(', "@typescript-eslint/recommended"', "")
    )
    authoring.lock(root)
    assert len(preset_releases.evaluated) == 2
    assert record(root)["presets"] == {"eslint": {"recommended": CORE}}


def test_a_new_lockfile_reads_every_preset_again(tmp_path, preset_releases):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    (root / "policy.toml").write_text(
        POLICY.replace('parser" = "8.73.0"', 'parser" = "8.74.0"')
    )
    authoring.lock(root)
    assert asked(preset_releases) == [
        {"eslint": ["recommended"], PLUGIN: ["recommended"]},
        {"eslint": ["recommended"], PLUGIN: ["recommended"]},
    ]
    assert record(root)["presets"] == RECORD["presets"]


def test_a_rule_file_without_presets_records_none_and_reads_none(
    tmp_path, preset_releases
):
    root = eslint_policy(tmp_path, RULES.replace("presets = [", "# presets = ["))
    locked = authoring.lock(root)
    assert record(root) == {"lockfile": lockfile(DEPENDENCIES)}
    assert preset_releases.evaluated == []
    assert len(locked.notes) == 1


def test_a_toml_configuration_with_a_presets_key_reads_none(tmp_path, preset_releases):
    root = docs(tmp_path, rules='presets = ["eslint/recommended"]\n')
    authoring.lock(root)
    assert preset_releases.evaluated == []
    lock = json.loads((root / "policy.lock").read_bytes())
    assert "presets" not in json.dumps(lock["components"]["prettier-docs"])


def test_each_reviewed_version_reads_and_renders_its_own_presets(
    tmp_path, preset_releases
):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    assert authoring.add(root, "eslint", "10.16.0").notes[1].message == NOTE
    assert [version for _, _, version, _ in preset_releases.evaluated] == [
        ESLINT,
        "10.16.0",
    ]
    policy = load_policy_tree(root)
    versions = policy.tools["eslint"].versions
    assert versions["10.16.0"].presets == (*CORE, *TYPESCRIPT)
    assert record(root, "10.16.0")["presets"] == RECORD["presets"]


@pytest.mark.parametrize(
    ("preset", "message", "step"),
    [
        (
            "@typescript-eslint/strictest",
            f"names the preset @typescript-eslint/strictest, but {PLUGIN} 8.73.0 "
            "has no preset strictest. It has functions, legacy, quiet-react, "
            "react, recommended, recommended-type-checked, strict.",
            "Correct the name in presets, then run gateseal policy lock.",
        ),
        (
            "@typescript-eslint/recommended-type-checked",
            "names the preset @typescript-eslint/recommended-type-checked, whose "
            "rule @typescript-eslint/await-thenable needs type information, but "
            "eslint runs outside the repository's Node project.",
            "Remove @typescript-eslint/recommended-type-checked from presets, or "
            "name it in the rule file of a node-project tool, such as eslint-typed.",
        ),
        (
            "@typescript-eslint/legacy",
            "names the preset @typescript-eslint/legacy, which gateseal cannot "
            "record as ESLint data: The preset @typescript-eslint/legacy[0] "
            "requires exactly rules.",
            f"Remove @typescript-eslint/legacy from presets, or name a flat "
            f"configuration of {PLUGIN}.",
        ),
        (
            "@typescript-eslint/functions",
            "names the preset @typescript-eslint/functions, which gateseal cannot "
            "record as ESLint data: The preset @typescript-eslint/functions[0].files "
            "must be a nonempty printable string.",
            f"Remove @typescript-eslint/functions from presets, or name a flat "
            f"configuration of {PLUGIN}.",
        ),
        (
            "@typescript-eslint/react",
            "names the preset @typescript-eslint/react, which turns on the rule "
            "react/jsx-key, but its plugins declare no react.",
            "Declare react in plugins, or remove @typescript-eslint/react from "
            "presets. Then run gateseal policy lock.",
        ),
    ],
    ids=["unknown", "typed", "legacy", "function", "undeclared plugin"],
)
def test_lock_refuses_a_preset_that_gateseal_cannot_record(
    tmp_path,
    preset_releases,  # noqa: F811
    preset,
    message,
    step,
):
    root = eslint_policy(
        tmp_path, RULES.replace('"@typescript-eslint/recommended"', f'"{preset}"')
    )
    failure = refused(authoring.lock, root)
    assert (failure.code, str(failure), failure.next_step) == (
        "invalid_field",
        f"The eslint configuration {message}",
        step,
    )
    assert not (root / "policy.lock").exists()


TYPED_POLICY = POLICY.replace(
    "typescript.tools.eslint", "typescript-typed-lint.tools.eslint-typed"
).replace("rules/eslint.toml", "rules/eslint-typed.toml")
TYPED_RULES = RULES.replace(
    '"eslint/recommended", "@typescript-eslint/recommended"',
    '"@typescript-eslint/recommended-type-checked"',
)


def test_a_node_project_tool_records_a_preset_whose_rules_need_types(
    tmp_path, preset_releases
):
    """The built-in eslint-typed reads the project's types, so it may name one."""
    files = {
        "rules/gitleaks.toml": None,
        "tools/yamllint.toml": None,
        "tools/gitleaks.toml": None,
        "rules/eslint-typed.toml": TYPED_RULES,
    }
    root = team(tmp_path, TYPED_POLICY, files)
    assert authoring.lock(root).code == "locked"
    lock = json.loads((root / "policy.lock").read_bytes())
    typed = PRESETS[PLUGIN]["recommended-type-checked"]["blocks"]
    record = lock["components"]["eslint-typed"]["versions"][ESLINT]
    assert record["presets"] == {PLUGIN: {"recommended-type-checked": typed}}
    locked, _ = consumer(load_policy_tree(root), ["typescript-typed-lint"])
    data = locked.snapshot.outputs[".gateseal/eslint-typed.json"]
    assert json.loads(data)["layers"] == [
        {"files": ["**/*.ts"], "rules": typed[0]["rules"]},
        {"files": ["**/*.ts"], "rules": {"no-var": "off"}},
    ]


def test_a_package_without_presets_says_it_has_none(tmp_path, preset_releases):
    preset_releases.presets = PRESETS | {"eslint": {}}
    failure = refused(authoring.lock, eslint_policy(tmp_path))
    assert str(failure) == (
        "The eslint configuration names the preset eslint/recommended, but eslint "
        f"{ESLINT} has no preset recommended. It has none."
    )


def test_a_preset_may_turn_off_a_rule_of_an_undeclared_plugin(
    tmp_path, preset_releases
):
    rules = RULES.replace(
        '"@typescript-eslint/recommended"', '"@typescript-eslint/quiet-react"'
    )
    root = eslint_policy(tmp_path, rules)
    assert authoring.lock(root).code == "locked"
    assert load_policy_tree(root).tools["eslint"].release.presets[-1] == QUIET


@pytest.mark.parametrize(
    ("rules", "named"),
    [
        (
            RULES.replace(', "@typescript-eslint/recommended"', ""),
            '{"eslint": ["recommended"]}',
        ),
        (
            RULES.replace(
                '"@typescript-eslint/recommended"', '"@typescript-eslint/strict"'
            ),
            f'{{"{PLUGIN}": ["strict"], "eslint": ["recommended"]}}',
        ),
        (RULES.replace("presets = [", "# presets = ["), "{}"),
        (
            RULES.replace(
                '"@typescript-eslint" = "@typescript-eslint/eslint-plugin"',
                '"@typescript-eslint" = "@typescript-eslint/parser"',
            ),
            '{"@typescript-eslint/parser": ["recommended"], "eslint": ["recommended"]}',
        ),
    ],
    ids=["dropped", "replaced", "none", "remapped"],
)
def test_a_record_of_other_presets_is_stale(tmp_path, preset_releases, rules, named):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    (root / "rules/eslint.toml").write_text(rules)
    failure = refused(load_policy_tree, root)
    assert (failure.code, failure.next_step) == ("policy_lock_stale", STALE_LOCK)
    assert str(failure) == (
        f'policy.lock records the presets {{"{PLUGIN}": ["recommended"], '
        f'"eslint": ["recommended"]}} for eslint {ESLINT}, but its '
        f"configuration names {named}."
    )
    assert authoring.lock(root).code == "locked"


def test_a_kept_preset_whose_plugin_is_no_longer_declared_is_stale(
    tmp_path, preset_releases
):
    rules = RULES.replace(
        '[plugins]\n"@typescript-eslint" = "@typescript-eslint/eslint-plugin"\n',
        '[plugins]\n"@typescript-eslint" = "@typescript-eslint/eslint-plugin"\n'
        'react = "@typescript-eslint/parser"\n',
    ).replace('"@typescript-eslint/recommended"', '"@typescript-eslint/react"')
    root = eslint_policy(tmp_path, rules)
    authoring.lock(root)
    (root / "rules/eslint.toml").write_text(
        rules.replace('react = "@typescript-eslint/parser"\n', "")
    )
    stale = refused(load_policy_tree, root)
    assert (stale.code, str(stale)) == (
        "policy_lock_stale",
        f"policy.lock records the preset @typescript-eslint/react of eslint {ESLINT} "
        "with the rule react/jsx-key, but its configuration's plugins declare no "
        "react.",
    )
    assert "react/jsx-key, but its plugins declare no react" in str(
        refused(authoring.lock, root)
    )


FIELD = f"components.eslint.versions.{ESLINT}.presets.eslint.recommended"


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ({"rules": {}}, f"{FIELD} must be an array."),
        ([1], f"{FIELD}[0] must be an object."),
        ([{"files": ["*.ts"]}], f"{FIELD}[0] requires exactly files, rules."),
        ([{"plugins": {}, "rules": {}}], f"{FIELD}[0] requires exactly rules."),
        ([{"files": [1], "rules": {}}], f"{FIELD}[0].files must be a nonempty"),
        ([{"files": [["*.ts", 1]], "rules": {}}], f"{FIELD}[0].files must be a"),
        ([{"ignores": [["*.ts"]], "rules": {}}], f"{FIELD}[0].ignores must be a"),
        ([{"rules": []}], f"{FIELD}[0].rules must be an object."),
        ([{"rules": {"no-var": 2}}], f"{FIELD}[0] sets the rule no-var without a"),
    ],
    ids=[
        "object",
        "number",
        "no rules",
        "plugins",
        "number pattern",
        "number in an and",
        "ignores and",
        "rules array",
        "numeric severity",
    ],
)
def test_an_edited_preset_record_is_an_edited_lock(
    tmp_path, preset_releases, blocks, message
):
    root = eslint_policy(tmp_path)
    authoring.lock(root)

    def edit(record):
        record["presets"]["eslint"]["recommended"] = blocks
        return record

    edit_presets(root, edit)
    failure = refused(load_policy_tree, root)
    assert failure.code == "policy_lock_edited"
    assert str(failure).startswith(f"policy.lock: {message}")


def test_a_preset_record_takes_files_and_ignores_as_eslint_does(
    tmp_path, preset_releases
):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    block = {
        "files": ["*.ts", ["src/**", "!**/*.d.ts"]],
        "ignores": ["dist/**"],
        "rules": {"no-var": ["warn", {"x": 1}]},
    }

    def edit(record):
        record["presets"]["eslint"]["recommended"] = [block]
        return record

    edit_presets(root, edit)
    assert load_policy_tree(root).tools["eslint"].release.presets[0] == block


def test_a_record_with_another_field_is_an_edited_lock(tmp_path, preset_releases):
    root = eslint_policy(tmp_path)
    authoring.lock(root)
    edit_presets(root, lambda record: record | {"rules": {}})
    failure = refused(load_policy_tree, root)
    assert (failure.code, str(failure)) == (
        "policy_lock_edited",
        f"policy.lock: components.eslint.versions.{ESLINT} requires exactly "
        "lockfile, presets.",
    )
