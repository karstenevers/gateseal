import hashlib
import json
import shutil
import tomllib

import pytest
from conftest import BUNDLED_MINIMUM, BUNDLED_TOOLS, PIN, reviewed_line
from gateseal.diagnostics import GatesealError, Result, redact
from gateseal.hook_results import hook_results
from gateseal.lock import compile_outputs
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import (
    Asset,
    bundled_root,
    content_digest,
    content_stream,
    load_bundled_policy,
    load_policy_stream,
    load_policy_tree,
)
from gateseal.tools import built_in_tools
from gateseal.validation import canonical, decode_json


@pytest.mark.parametrize(
    "data",
    [
        b'{"a":1,"a":2}',
        b'{"value":NaN}',
        b'{"v":Infinity}',
        b'{"v":1e9999}',
        b'{"v":-1e9999}',
        b"\xff",
    ],
)
def test_strict_json(data):
    with pytest.raises(GatesealError) as failure:
        decode_json(data)
    assert failure.value.code == "invalid_json"


def test_duplicate_json_key_is_named():
    with pytest.raises(GatesealError, match="Duplicate JSON key 'a'"):
        decode_json(b'{"a":1,"a":2}')


def tree(tmp_path):
    policy = load_bundled_policy()
    for asset in policy.assets:
        (tmp_path / asset.path).parent.mkdir(exist_ok=True)
        (tmp_path / asset.path).write_bytes(asset.content)
    return policy


def relock(root, change):
    """Rewrite root's policy.lock as gateseal policy lock would, after change."""
    path = root / "policy.lock"
    document = json.loads(path.read_bytes())
    change(document["components"])
    path.write_bytes(canonical(document))


def test_policy_is_one_immutable_snapshot(tmp_path):
    bundled = tree(tmp_path)
    copied = load_policy_tree(tmp_path)
    shutil.rmtree(tmp_path)
    assert copied == bundled
    assert tuple(hook.id for hook in copied.tools["pre-commit-hooks"].use.hooks) == (
        "trailing-whitespace",
        "end-of-file-fixer",
        "check-yaml",
    )
    with pytest.raises(AttributeError):
        copied.release = "changed"


def test_digest_has_independent_known_vector():
    assert content_digest((Asset("policy.toml", b"schema = 1\n"),)) == (
        "sha256:ca3d3d69519df632243370e3876216ce87d3d285743cefa037c16c508b38836b"
    )


HOOKS = 'hook_ids = ["trailing-whitespace", "end-of-file-fixer", "check-yaml"]'
HYGIENE = "[capabilities.hygiene.tools.pre-commit-hooks]"
PINNED_HOOKS = 'pin = "6.0.0"'
RULES = 'configuration = "rules/betterleaks.toml"'
TARGET = '[capabilities.python.choices]\ntarget_version = "py311"\n'
RUFF_VERSION = BUNDLED_TOOLS["ruff"].pin
RUFF_PIN = f'pin = "{RUFF_VERSION}"'
RUFF_REVIEWED = reviewed_line("ruff")
PYTHON = (
    '[capabilities.python.tools.ruff]\nhook_ids = ["ruff-check", "ruff-format"]\n'
    f'configuration = "rules/ruff.toml"\n{RUFF_PIN}\n{RUFF_REVIEWED}\n\n'
)


@pytest.mark.parametrize(
    "change,replacement,category",
    [
        ("schema = 5", "schema = 4", "unsupported_schema"),
        ("schema = 5", "schema = 6\nsurprise = 1", "unsupported_schema"),
        (
            BUNDLED_MINIMUM,
            'minimum_compiler = "99.0.0"\nsurprise = 1',
            "incompatible_compiler",
        ),
        (HOOKS, 'hook_ids = ["check-yaml", "check-yaml"]', "duplicate_identifier"),
        (HYGIENE, 'extends = "base.toml"\n\n' + HYGIENE, "policy_inheritance"),
        (HYGIENE, 'surprise = "value"\n\n' + HYGIENE, "invalid_field"),
        (HYGIENE, '[runtime]\nminimum_uv = "0.13.5"\n\n' + HYGIENE, "invalid_field"),
        (RULES, 'configuration = "rules/betterleaks.json"', "invalid_field"),
        (RULES, 'configuration = "rules/Betterleaks.toml"', "unsafe_path"),
        (RULES, 'configuration = "rules/../betterleaks.toml"', "unsafe_path"),
        (RULES, 'configuration = "betterleaks.toml"', "invalid_field"),
        (RULES, 'rules = "rules/betterleaks.toml"', "invalid_field"),
        (RULES, RULES + "\nhook_ids = []", "unsupported_hook"),
        (PINNED_HOOKS, PINNED_HOOKS + '\nrevision = "3e8a87"', "invalid_field"),
        (
            f'pin = "{BUNDLED_TOOLS["betterleaks"].pin}"',
            'pin = "1.7.0"',
            "invalid_field",
        ),
        (
            PINNED_HOOKS,
            PINNED_HOOKS + '\nrequires."6.0.0" = { pre-commit = "3.2.0" }',
            "invalid_field",
        ),
        (
            PINNED_HOOKS,
            PINNED_HOOKS + '\nrequires."6.0.0" = { ruff = "9.0.0" }',
            "incompatible_policy",
        ),
        ("exclude = []", "hook_ids = []", "unsupported_choice"),
        ("exclude = []", 'exclude = ["/etc"]', "invalid_choice"),
        ('target_version = "py311"', 'target_version = "py27"', "invalid_choice"),
        ('target_version = "py311"', "target_version = []", "invalid_choice"),
    ],
    ids=lambda value: value if isinstance(value, str) and "_" in value else None,
)
def test_policy_rejects_invalid_inputs(tmp_path, change, replacement, category):
    """Validate schema and minimum_compiler first, then every field exactly.

    Both stop before any field the compiler might not know.
    """
    tree(tmp_path)
    path = tmp_path / "policy.toml"
    text = path.read_text()
    assert change in text
    path.write_text(text.replace(change, replacement, 1))
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(tmp_path)
    assert failure.value.code == category


def test_policy_names_its_reviewed_versions():
    tools = load_bundled_policy().tools
    written = tomllib.loads((bundled_root() / "policy.toml").read_text())
    assert {name: (t.pin, list(t.versions)) for name, t in tools.items()} == {
        name: (table["pin"], table["reviewed"])
        for capability in written["capabilities"].values()
        for name, table in capability["tools"].items()
    }
    assert tools["pre-commit-hooks"].versions["5.0.0"].revision == (
        "cef0300fd0fc4d2a87a85fa2093c6b283ea36f4b"
    )


def test_policy_content_stream_round_trips():
    policy = load_bundled_policy()
    stream = content_stream(policy.assets)
    assert "sha256:" + hashlib.sha256(stream).hexdigest() == policy.content_digest
    assert load_policy_stream(stream) == policy
    for damaged in (stream[:-1], b"x" + stream, stream[:40]):
        with pytest.raises(GatesealError):
            load_policy_stream(damaged)


def test_secret_rules_reject_carriage_returns(tmp_path):
    """Reject a CR in secret rules.

    Owned outputs are compared with LF line endings, so rules copied into one
    must not hold a CR.
    """
    tree(tmp_path)
    path = tmp_path / "rules" / "betterleaks.toml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(GatesealError, match="LF line endings"):
        load_policy_tree(tmp_path)


def test_policy_opaque_release_is_not_compiler_version(tmp_path):
    bundled = tree(tmp_path)
    path = tmp_path / "policy.toml"
    path.write_text(
        path.read_text().replace(bundled.release, "reviewed release \u00fc")
    )
    assert load_policy_tree(tmp_path).release == "reviewed release \u00fc"


RELEASE = "https://github.com/betterleaks/betterleaks/releases/download/"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/betterleaks/betterleaks/releases/download/v1.9.0/b.tar.gz",
        RELEASE + "v1.9.0/betterleaks_1.9.0_linux_x64.tar.gz?token=x",
        RELEASE + "v1.9.0/betterleaks_1.9.0_linux_x64.zip",
        RELEASE + "v1.8.0/betterleaks_1.9.0_linux_x64.tar.gz",
        RELEASE + "v1.9.0/nested/betterleaks_1.9.0_linux_x64.tar.gz",
    ],
    ids=["other host", "query", "not tar.gz", "other version", "nested"],
)
def test_betterleaks_archive_must_be_the_pinned_release(tmp_path, url):
    tree(tmp_path)

    def change(components):
        platforms = components["betterleaks"]["versions"]["1.9.0"]["platforms"]
        platforms["linux-x64"]["url"] = url

    relock(tmp_path, change)
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(tmp_path)
    assert failure.value.code == "policy_lock_edited"


@pytest.mark.parametrize(
    "change,category",
    [
        (("platform", "windows-x64"), "policy_lock_stale"),
        (("drop", "linux-x64"), "policy_lock_stale"),
        (("sha256", "F" * 64), "policy_lock_edited"),
        (("executable", "bin/betterleaks"), "policy_lock_edited"),
        (("executable", ".."), "policy_lock_edited"),
        (("size", 1), "policy_lock_edited"),
    ],
)
def test_betterleaks_records_are_validated(tmp_path, change, category):
    tree(tmp_path)
    field, value = change

    def edit(components):
        platforms = components["betterleaks"]["versions"]["1.9.0"]["platforms"]
        if field == "platform":
            platforms[value] = platforms.pop("linux-x64")
        elif field == "drop":
            del platforms[value]
        else:
            platforms["linux-x64"][field] = value

    relock(tmp_path, edit)
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(tmp_path)
    assert failure.value.code == category
    assert failure.value.next_step == (
        "Run gateseal policy lock in the policy directory."
    )


@pytest.mark.parametrize(
    "rules",
    [
        '[extend]\npath = "team.toml"\n',
        '[extend]\nurl = "https://example.com/rules.toml"\n',
        '[extend]\nuseDefault = true\npath = "team.toml"\n',
        'extend = "team.toml"\n',
    ],
)
def test_secret_rules_cannot_extend_another_configuration(tmp_path, rules):
    tree(tmp_path)
    (tmp_path / "rules" / "betterleaks.toml").write_text(rules)
    with pytest.raises(GatesealError, match="must not extend"):
        load_policy_tree(tmp_path)


@pytest.mark.parametrize(
    "extend",
    ["useDefault = true", 'useDefault = true\ndisabledRules = ["generic-password"]'],
)
def test_secret_rules_can_start_from_the_rules_built_into_betterleaks(tmp_path, extend):
    tree(tmp_path)
    (tmp_path / "rules" / "betterleaks.toml").write_text(f"[extend]\n{extend}\n")
    load_policy_tree(tmp_path)


def test_policy_without_secrets_still_loads(tmp_path):
    bundled = tree(tmp_path)
    path = tmp_path / "policy.toml"
    before, secrets = path.read_text().split("\n[capabilities.secrets.")
    path.write_text(before + secrets.split("\n\n", 1)[1])
    (tmp_path / "rules" / "betterleaks.toml").unlink()
    relock(tmp_path, lambda components: components.pop("betterleaks"))
    policy = load_policy_tree(tmp_path)
    assert policy.capabilities == (
        "hygiene",
        "python",
        "python-types",
        "typescript",
        "typescript-types",
    )
    assert "betterleaks" not in policy.tools
    assert [asset.path for asset in policy.assets] == [
        "policy.toml",
        "rules/ruff.toml",
        "rules/pyright.toml",
        "rules/eslint.toml",
        "rules/prettier.toml",
        "policy.lock",
    ]
    assert policy.content_digest != bundled.content_digest


def test_policy_without_python_offers_no_python(tmp_path):
    tree(tmp_path)
    path = tmp_path / "policy.toml"
    text = path.read_text()
    assert PYTHON + TARGET + "\n" in text
    path.write_text(text.replace(PYTHON + TARGET + "\n", ""))
    (tmp_path / "rules" / "ruff.toml").unlink()
    relock(tmp_path, lambda components: components.pop("ruff"))
    policy = load_policy_tree(tmp_path)
    assert policy.capabilities == (
        "hygiene",
        "python-types",
        "secrets",
        "typescript",
        "typescript-types",
    )
    assert "ruff" not in policy.tools
    assert "rules/ruff.toml" not in {asset.path for asset in policy.assets}
    manifest = parse_manifest(
        render(("hygiene", "python"), BundledSource(policy.release))
    )
    with pytest.raises(GatesealError, match="offers no python") as failure:
        resolve(manifest, policy)
    assert failure.value.code == "unsupported_capability"


RUFF = 'configuration = "rules/ruff.toml"'


@pytest.mark.parametrize(
    "change,category",
    [
        (('"ruff-check", "ruff-format"', '"ruff-check", "ruff"'), "unsupported_hook"),
        (
            ('"ruff-check", "ruff-format"', '"ruff-check", "ruff-check"'),
            "duplicate_identifier",
        ),
        (('"ruff-check", "ruff-format"', '"check-yaml"'), "unsupported_hook"),
        ((RUFF_PIN, 'pin = "0.17"'), "invalid_field"),
        ((RUFF_PIN + "\n", ""), "invalid_field"),
        ((RUFF_REVIEWED, 'reviewed = ["0.17"]'), "invalid_field"),
        ((RUFF_REVIEWED, RUFF_REVIEWED.replace("[", '["0.17.1", ')), "invalid_field"),
        ((RUFF_REVIEWED, "reviewed = []"), "invalid_field"),
        (
            (
                RUFF_REVIEWED,
                RUFF_REVIEWED
                + f'\nrequires."{RUFF_VERSION}" = {{ betterleaks = "9.0.0" }}',
            ),
            "incompatible_policy",
        ),
        (
            (
                RUFF_REVIEWED,
                RUFF_REVIEWED + f'\nrequires."{RUFF_VERSION}" = {{ ruff = "0.1.0" }}',
            ),
            "invalid_field",
        ),
        (
            (RUFF, 'configuration = "rules/../ruff.toml"'),
            "unsafe_path",
        ),
        ((RUFF + "\n", ""), "invalid_field"),
        ((RUFF, 'ruff = "0.17.1"'), "invalid_field"),
    ],
    ids=[
        "hook",
        "duplicate hook",
        "hygiene hook",
        "pin",
        "no pin",
        "version",
        "version twice",
        "no version",
        "requirement",
        "requirement of itself",
        "path",
        "no configuration",
        "schema 1 field",
    ],
)
def test_python_table_and_ruff_catalog_are_validated(tmp_path, change, category):
    tree(tmp_path)
    path = tmp_path / "policy.toml"
    old, new = change
    assert old in path.read_text()
    path.write_text(path.read_text().replace(old, new, 1))
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(tmp_path)
    assert failure.value.code == category


def ruff_toml(policy, extra=""):
    """Return the .gateseal/ruff.toml that policy renders for python."""
    manifest = parse_manifest(
        render(("hygiene", "python"), BundledSource(policy.release)) + extra.encode()
    )
    outputs = compile_outputs(
        resolve(manifest, policy), PIN, BundledSource(policy.release)
    )
    return outputs[".gateseal/ruff.toml"]


@pytest.mark.parametrize(
    "rules", [b"", b'target-version = "py38"\n'], ids=["no key", "own key"]
)
def test_a_policy_that_hides_the_target_version_keeps_its_ruff_bytes(tmp_path, rules):
    tree(tmp_path)
    configuration = tmp_path / "rules" / "ruff.toml"
    configuration.write_bytes(rules + configuration.read_bytes())
    policy = tmp_path / "policy.toml"
    policy.write_text(policy.read_text().replace(TARGET + "\n", ""))
    hidden = load_policy_tree(tmp_path)
    assert ruff_toml(hidden) == configuration.read_bytes()
    with pytest.raises(GatesealError) as failure:
        ruff_toml(hidden, '\n[choices.python]\ntarget_version = "py39"\n')
    assert failure.value.code == "unsupported_choice"


def test_a_ruff_configuration_cannot_set_the_target_version_it_exposes(tmp_path):
    tree(tmp_path)
    configuration = tmp_path / "rules" / "ruff.toml"
    configuration.write_bytes(b'target-version = "py38"\n' + configuration.read_bytes())
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(tmp_path)
    assert failure.value.code == "invalid_field"
    assert str(failure.value) == (
        "The ruff configuration sets target-version, which "
        "capabilities.python.choices.target_version sets for each repository."
    )


def pin_ruff_0_4_0(tmp_path, default):
    path = tmp_path / "policy.toml"
    path.write_text(
        path.read_text()
        .replace('target_version = "py311"', f'target_version = "{default}"')
        .replace(RUFF_PIN, 'pin = "0.4.0"')
        .replace(RUFF_REVIEWED, RUFF_REVIEWED.replace("]", ', "0.4.0"]'))
    )

    def review(components):
        components["ruff"]["versions"]["0.4.0"] = {"revision": "0" * 40}

    relock(tmp_path, review)


def test_a_ruff_pin_must_accept_the_default_target_version(tmp_path):
    tree(tmp_path)
    pin_ruff_0_4_0(tmp_path, "py314")
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(tmp_path)
    assert failure.value.code == "invalid_choice"
    assert str(failure.value).startswith(
        "capabilities.python.choices.target_version is py314, which ruff 0.4.0 "
        "does not accept. ruff 0.4.0 accepts py37, py38, py39, py310, py311, py312."
    )


def test_the_selected_ruff_must_accept_the_chosen_target_version(tmp_path):
    tree(tmp_path)
    pin_ruff_0_4_0(tmp_path, "py312")
    policy = load_policy_tree(tmp_path)
    with pytest.raises(GatesealError) as failure:
        ruff_toml(policy, '\n[choices.python]\ntarget_version = "py314"\n')
    assert failure.value.code == "invalid_choice"
    assert str(failure.value).startswith(
        "choices.python.target_version is py314, which ruff 0.4.0 does not accept."
    )
    assert ruff_toml(policy) == (
        b'target-version = "py312"\n' + (tmp_path / "rules" / "ruff.toml").read_bytes()
    )


def test_ruff_configuration_cannot_extend_another_configuration(tmp_path):
    tree(tmp_path)
    path = tmp_path / "rules" / "ruff.toml"
    path.write_text('extend = "../pyproject.toml"\n' + path.read_text())
    with pytest.raises(GatesealError) as refused:
        load_policy_tree(tmp_path)
    assert str(refused.value) == (
        "The ruff configuration must not extend another configuration. A "
        "Betterleaks or Gitleaks extend table may hold only useDefault and "
        "disabledRules."
    )


@pytest.mark.parametrize("extends", ['"../pyrightconfig.json"', '""'])
def test_pyright_configuration_cannot_extend_another_configuration(tmp_path, extends):
    tree(tmp_path)
    path = tmp_path / "rules" / "pyright.toml"
    path.write_text(path.read_text() + f"extends = {extends}\n")
    with pytest.raises(GatesealError) as refused:
        load_policy_tree(tmp_path)
    assert refused.value.code == "invalid_field"
    assert str(refused.value) == (
        "The pyright configuration must not extend another configuration: "
        "remove tool.pyright.extends."
    )


@pytest.mark.parametrize(
    "plugins",
    [
        'plugins = ["prettier-plugin-x"]\n',
        "plugins = []\n",
        '[[overrides]]\nfiles = "*.ts"\n\n[[overrides]]\nfiles = "*.tsx"\n'
        '[overrides.options]\nplugins = ["prettier-plugin-x"]\n',
    ],
    ids=["top level", "empty", "second override"],
)
def test_prettier_configuration_cannot_name_plugins(tmp_path, plugins):
    tree(tmp_path)
    path = tmp_path / "rules" / "prettier.toml"
    path.write_text(plugins + path.read_text())
    with pytest.raises(GatesealError) as refused:
        load_policy_tree(tmp_path)
    assert refused.value.code == "invalid_field"
    assert str(refused.value) == (
        "The prettier configuration must not name plugins, also not in the "
        "options of an override: Prettier loads a plugin from the repository, "
        "where no policy pins it."
    )


@pytest.mark.parametrize(
    "text",
    [
        '[[overrides]]\nfiles = "*.ts"\nplugins = "x"\n[overrides.options]\nsemi = 1\n',
        '[overrides]\nplugins = "x"\n',
        '[other.options]\nplugins = "x"\n',
        'overrides = ["plugins"]\n',
    ],
    ids=["beside options", "overrides table", "other table", "override string"],
)
def test_a_configuration_may_name_plugins_outside_prettier_s_options(tmp_path, text):
    tree(tmp_path)
    path = tmp_path / "rules" / "prettier.toml"
    path.write_text(path.read_text() + text)
    load_policy_tree(tmp_path)


@pytest.mark.parametrize("name", ["ruff", "pyright", "betterleaks"])
def test_a_configuration_of_a_tool_outside_node_may_name_plugins(tmp_path, name):
    tree(tmp_path)
    path = tmp_path / "rules" / f"{name}.toml"
    path.write_text('plugins = ["x"]\n' + path.read_text())
    load_policy_tree(tmp_path)


def test_a_configuration_may_name_extends_outside_pyright_s_table(tmp_path):
    tree(tmp_path)
    path = tmp_path / "rules" / "pyright.toml"
    path.write_text(path.read_text() + '\n[tool.other]\nextends = "x"\n')
    load_policy_tree(tmp_path)


def test_ruff_configuration_rejects_carriage_returns(tmp_path):
    tree(tmp_path)
    path = tmp_path / "rules" / "ruff.toml"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    with pytest.raises(GatesealError, match="ruff configuration must use LF"):
        load_policy_tree(tmp_path)


@pytest.mark.parametrize("asset", ["rules/betterleaks.toml", "rules/ruff.toml"])
def test_digest_covers_the_capability_assets(tmp_path, asset):
    bundled = tree(tmp_path)
    assert asset in {item.path for item in bundled.assets}
    path = tmp_path / asset
    path.write_bytes(path.read_bytes() + b"\n")
    assert load_policy_tree(tmp_path).content_digest != bundled.content_digest


def test_digest_covers_every_fact_of_the_policy_lock(tmp_path):
    """A fact that only upstream can refute still changes the digest.

    Offline, a well-formed SHA-256 cannot be checked; the Lock's digest still
    binds the consumer to the one that the policy release holds.
    """
    bundled = tree(tmp_path)

    def change(components):
        platforms = components["betterleaks"]["versions"]["1.8.0"]["platforms"]
        platforms["linux-x64"]["sha256"] = "0" * 64

    relock(tmp_path, change)
    assert load_policy_tree(tmp_path).content_digest != bundled.content_digest


def test_policy_rejects_symlink_asset_and_parent(tmp_path):
    directory = tmp_path / "policy"
    directory.mkdir()
    tree(directory)
    outside = tmp_path / "outside"
    outside.write_bytes((directory / "policy.lock").read_bytes())
    (directory / "policy.lock").unlink()
    (directory / "policy.lock").symlink_to(outside)
    with pytest.raises(GatesealError):
        load_policy_tree(directory)
    assert outside.read_bytes()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(GatesealError):
        load_policy_tree(alias)


def test_all_contracts_reject_unknown_or_boolean_schema(installed, configured):
    from conftest import git_path
    from gateseal.lock import load

    preparation = git_path(configured, "gateseal")
    locked = load(configured, preparation)
    for key, value in (("schema", True), ("surprise", "value")):
        document = json.loads(locked.snapshot.lock)
        document[key] = value
        (configured / "gateseal.lock").write_text(json.dumps(document))
        with pytest.raises(GatesealError):
            load(configured, preparation)


def test_diagnostics_redact_complete_output():
    result = Result(
        "run",
        "failed",
        details="https://user:secret@host/x?jwt=hidden\n"
        "Authorization: Bearer hidden\nghp_abcdef",
    )
    rendered = result.render(True, True)
    for secret in ("secret", "hidden", "ghp_abcdef"):
        assert secret not in rendered
    assert json.loads(rendered)["schema"] == 1


@pytest.mark.parametrize("ending", ["-", "_"])
def test_diagnostics_redact_whole_github_tokens(ending):
    installation = (
        "ghs_"
        + "1234567_"
        + "aB0_" * 8
        + "a."
        + "cD1-" * 80
        + "."
        + "eF2_" * 43
        + ending
    )
    for token in ("ghs_" + "a" * 36, installation):
        complete = redact(f"before {token} after") == "before [redacted] after"
        assert complete
        result = Result("run", message=token, details=token)
        human_complete = result.render(False, True) == "[redacted]\n[redacted]"
        assert human_complete
        json_complete = all(
            item["message"] == "[redacted]"
            for item in json.loads(result.render(True, True))["diagnostics"]
        )
        assert json_complete


def test_diagnostics_keep_short_github_token_suffixes():
    kept = redact("ghp_" + "abcdef.header-payload") == "[redacted].header-payload"
    assert kept


def test_diagnostics_keep_the_port_and_the_ssh_user():
    rendered = Result(
        "check",
        "policy_fetch_failed",
        "Git could not read ssh://git@host:2222/team/policy.git, "
        "https://token@host:8443/team/policy.git or ssh://git:secret@host/x",
    ).render(False)
    assert "ssh://git@host:2222/team/policy.git" in rendered
    assert "https://host:8443/team/policy.git" in rendered
    assert "ssh://host/x" in rendered
    assert "token" not in rendered and "secret" not in rendered


RUN_OUTPUT = """\
ruff check...............................................................Failed
- hook id: ruff-check
- exit code: 1
- files were modified by this hook

src/a b.py:2:5: F821 Undefined name `undefined_name`
src/c.py:1:8: F401 [*] `os` imported but unused
bad.py:1:7: invalid-syntax: Expected a parameter or the end of the parameter list
Found 4 errors (1 fixed, 3 remaining).

ruff format..........................................(no files to check)Skipped
check yaml...............................................................Passed
Detect secrets with Betterleaks..........................................Failed
- hook id: betterleaks
- exit code: 1

config.json:3:12: generic-api-key (Generic API key assignment)
keys.env:1:5: _team_access.token (A team rule)
keys.env:2:1: 123-token (A team rule)
keys.env:3:1: ns:token (A team rule)
Next: Remove each secret, stage the fix, and retry.
"""


def hooks(*ids):
    """Return the built-in hooks with ids."""
    tools = built_in_tools().values()
    return tuple(
        hook for tool in tools for hook in tool.hooks.values() if hook.id in ids
    )


def test_hook_results_name_each_hook_and_finding():
    selected = hooks("ruff-check", "ruff-format", "check-yaml", "betterleaks")
    results = hook_results(RUN_OUTPUT, selected, "run")
    summary = [
        (r["category"], r["hook"], r.get("status"), r.get("file"), r.get("rule"))
        for r in results
    ]
    assert summary == [
        ("hook", "ruff-check", "failed", None, None),
        ("finding", "ruff-check", None, "src/a b.py", "F821"),
        ("finding", "ruff-check", None, "src/c.py", "F401"),
        ("finding", "ruff-check", None, "bad.py", "invalid-syntax"),
        ("hook", "ruff-format", "skipped", None, None),
        ("hook", "check-yaml", "passed", None, None),
        ("hook", "betterleaks", "failed", None, None),
        ("finding", "betterleaks", None, "config.json", "generic-api-key"),
        ("finding", "betterleaks", None, "keys.env", "_team_access.token"),
        ("finding", "betterleaks", None, "keys.env", "123-token"),
        ("finding", "betterleaks", None, "keys.env", "ns:token"),
    ]
    check, unused = results[0], results[2]
    assert check["modified"] is True and check["tool"] == "ruff"
    assert unused == {
        "severity": "error",
        "category": "finding",
        "tool": "ruff",
        "origin": "built-in",
        "hook": "ruff-check",
        "stage": "run",
        "file": "src/c.py",
        "line": 1,
        "column": 8,
        "rule": "F401",
        "message": "`os` imported but unused",
    }
    assert results[4]["message"] == "ruff format: Skipped (no files to check)"
    assert results[4]["severity"] == "info"


def test_hook_results_read_colored_output_and_notebook_cells():
    output = (
        "\x1b[1mruff check\x1b[0m.........\x1b[41mFailed\x1b[m\n"
        "\x1b[2m- hook id: ruff-check\x1b[m\n\n"
        "\x1b[1mnb.ipynb\x1b[0m\x1b[36m:\x1b[0mcell 2\x1b[36m:\x1b[0m2"
        "\x1b[36m:\x1b[0m5\x1b[36m:\x1b[0m \x1b[1m\x1b[31mF821\x1b[0m "
        "Undefined name `undefined_name`\n"
    )
    hook, finding = hook_results(output, hooks("ruff-check"), "run")
    assert hook["status"] == "failed"
    assert (finding["file"], finding["cell"], finding["line"], finding["column"]) == (
        "nb.ipynb",
        2,
        2,
        5,
    )
    assert finding["rule"] == "F821"


def test_hook_results_ignore_unselected_hooks_and_their_output():
    assert hook_results(RUN_OUTPUT, hooks("check-yaml"), "run") == [
        {
            "severity": "info",
            "category": "hook",
            "tool": "pre-commit-hooks",
            "origin": "built-in",
            "hook": "check-yaml",
            "stage": "run",
            "status": "passed",
            "modified": False,
            "message": "check yaml: Passed",
        }
    ]


def test_result_entries_are_redacted_and_bounded():
    entries = tuple(
        {"category": "finding", "file": f"f{n}.py", "message": "ghp_abcdef"}
        for n in range(201)
    )
    result = Result("run", "checks_failed", "Failed.", 1, entries=entries)
    bounded = json.loads(result.render(True))["diagnostics"]
    assert len(bounded) == 1 + 200 + 1
    assert bounded[-1]["category"] == "truncated"
    assert "ghp_abcdef" not in json.dumps(bounded)
    complete = json.loads(result.render(True, True))["diagnostics"]
    assert [d.get("file") for d in complete[1:]] == [f"f{n}.py" for n in range(201)]
