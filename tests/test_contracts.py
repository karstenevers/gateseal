import hashlib
import json
import shutil

import pytest
from gateseal import bootstrap
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError, Result, redact
from gateseal.hook_results import hook_results
from gateseal.lock import Snapshot, compile_outputs, validate_snapshot
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import (
    Asset,
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
HYGIENE = "[capabilities.hygiene]"
PINNED_HOOKS = '[components.pre-commit-hooks.versions."6.0.0"]'
RULES = 'configuration = "rules/betterleaks.toml"'
TARGET = '[choices.python.target_version]\ndefault = "py311"\n'


@pytest.mark.parametrize(
    "change,replacement,category",
    [
        ("schema = 4", "schema = 3", "unsupported_schema"),
        ("schema = 4", "schema = 5\nsurprise = 1", "unsupported_schema"),
        (
            'minimum_compiler = "0.1.0"',
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
        ('pin = "1.9.0"', 'pin = "1.7.0"', "invalid_field"),
        ("[components.betterleaks]", "[components.black]", "unsupported_component"),
        (
            "[components.ruff]",
            '[components.pre-commit]\npin = "4.6.2"\n\n'
            '[components.pre-commit.versions."4.6.2"]\n\n[components.ruff]',
            "unsupported_component",
        ),
        (
            PINNED_HOOKS,
            PINNED_HOOKS + '\nrequires = { pre-commit = "3.2.0" }',
            "invalid_field",
        ),
        (
            PINNED_HOOKS,
            PINNED_HOOKS + '\nrequires = { ruff = "9.0.0" }',
            "incompatible_policy",
        ),
        (
            "[choices.hygiene.exclude]",
            "[choices.hygiene.hook_ids]",
            "unsupported_choice",
        ),
        ("default = []", 'default = ["/etc"]', "invalid_choice"),
        ('default = "py311"', 'default = "py27"', "invalid_choice"),
        ('default = "py311"', "default = []", "invalid_choice"),
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
    assert {name: (t.pin, sorted(t.versions)) for name, t in tools.items()} == {
        "betterleaks": ("1.9.0", ["1.8.0", "1.9.0"]),
        "pre-commit-hooks": ("6.0.0", ["5.0.0", "6.0.0"]),
        "ruff": ("0.17.1", ["0.17.1"]),
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


def test_secret_rules_cannot_extend_another_configuration(tmp_path):
    tree(tmp_path)
    path = tmp_path / "rules" / "betterleaks.toml"
    path.write_text("[extend]\nuseDefault = true\n\n" + path.read_text())
    with pytest.raises(GatesealError, match="must not extend"):
        load_policy_tree(tmp_path)


def test_policy_without_secrets_still_loads(tmp_path):
    bundled = tree(tmp_path)
    path = tmp_path / "policy.toml"
    text = path.read_text()
    for table in (
        '[capabilities.secrets]\ntools = ["betterleaks"]\n\n',
        '[tools.betterleaks]\nconfiguration = "rules/betterleaks.toml"\n\n',
    ):
        assert table in text
        text = text.replace(table, "")
    path.write_text(text.split("[components.betterleaks]")[0])
    (tmp_path / "rules" / "betterleaks.toml").unlink()
    relock(tmp_path, lambda components: components.pop("betterleaks"))
    policy = load_policy_tree(tmp_path)
    assert policy.capabilities == ("hygiene", "python")
    assert "betterleaks" not in policy.tools
    assert [asset.path for asset in policy.assets] == [
        "policy.toml",
        "rules/ruff.toml",
        "policy.lock",
    ]
    assert policy.content_digest != bundled.content_digest


def test_policy_without_python_offers_no_python(tmp_path):
    tree(tmp_path)
    path = tmp_path / "policy.toml"
    text = path.read_text()
    for table in (
        '[capabilities.python]\ntools = ["ruff"]\n\n',
        '[tools.ruff]\nhook_ids = ["ruff-check", "ruff-format"]\n'
        'configuration = "rules/ruff.toml"\n\n',
        TARGET,
    ):
        assert table in text
        text = text.replace(table, "")
    before, ruff = text.split("[components.ruff]\n")
    path.write_text(before + ruff[ruff.index("[components.betterleaks]") :])
    (tmp_path / "rules" / "ruff.toml").unlink()
    relock(tmp_path, lambda components: components.pop("ruff"))
    policy = load_policy_tree(tmp_path)
    assert policy.capabilities == ("hygiene", "secrets")
    assert "ruff" not in policy.tools
    assert "rules/ruff.toml" not in {asset.path for asset in policy.assets}
    manifest = parse_manifest(
        render(("hygiene", "python"), BundledSource(policy.release))
    )
    with pytest.raises(GatesealError, match="offers no python") as failure:
        resolve(manifest, policy)
    assert failure.value.code == "unsupported_capability"


RUFF_PIN = '[components.ruff.versions."0.17.1"]'
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
        (('pin = "0.17.1"', 'pin = "0.17"'), "invalid_field"),
        (('pin = "0.17.1"\n', ""), "invalid_field"),
        (('versions."0.17.1"]', 'versions."0.17"]'), "invalid_field"),
        (
            (RUFF_PIN, RUFF_PIN + '\nrequires = { betterleaks = "9.0.0" }'),
            "incompatible_policy",
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
        "requirement",
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
    wheel = "gateseal-0.1.0-py3-none-any.whl"
    artifact = CompilerArtifact("local-wheel", f"/{wheel}", wheel, "a" * 64, "0.1.0")
    outputs = compile_outputs(
        resolve(manifest, policy), artifact, BundledSource(policy.release)
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
        "choices.python.target_version sets for each repository."
    )


def pin_ruff_0_4_0(tmp_path, default):
    path = tmp_path / "policy.toml"
    path.write_text(
        path.read_text()
        .replace('default = "py311"', f'default = "{default}"')
        .replace('pin = "0.17.1"', 'pin = "0.4.0"')
        .replace(RUFF_PIN, RUFF_PIN + '\n\n[components.ruff.versions."0.4.0"]')
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
        "choices.python.target_version.default is py314, which ruff 0.4.0 does "
        "not accept. ruff 0.4.0 accepts py37, py38, py39, py310, py311, py312."
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
    with pytest.raises(GatesealError, match="must not extend"):
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
        snapshot = Snapshot(
            locked.snapshot.manifest,
            json.dumps(document).encode(),
            locked.snapshot.outputs,
        )
        with pytest.raises(GatesealError):
            validate_snapshot(configured, snapshot, preparation)


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


def test_hook_bytes_are_pinned():
    assert hashlib.sha256(bootstrap.HOOK).hexdigest() == (
        "c8b954813d52fc7394d2f9d5f8a3915525ac3132cc23cb7aa58154600e3050f6"
    ), "Changed hook bytes make every installed gateseal hook unmanaged."


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
