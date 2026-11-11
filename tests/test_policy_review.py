"""A policy's authors get each mistake in policy.toml named before a release.

Each test edits the bundled policy.toml in memory and reviews or loads it, as
gateseal policy check does for a policy's files.
"""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy import (
    load_bundled_policy,
    load_policy,
    load_policy_tree,
    review_policy,
)

BUNDLED = {asset.path: asset.content for asset in load_bundled_policy().assets}
HOOKS_6 = '[components.pre-commit-hooks.versions."6.0.0"]\n'
HOOKS_5 = '[components.pre-commit-hooks.versions."5.0.0"]\n'
RUFF = '[components.ruff.versions."0.17.1"]\n'


def reader(text):
    """Return a reader of the bundled policy's files with text as policy.toml."""
    files = {**BUNDLED, "policy.toml": text.encode()}

    def read_asset(name):
        if name not in files:
            raise FileNotFoundError(name)
        return files[name]

    return read_asset


def assets(old, new):
    text = BUNDLED["policy.toml"].decode()
    assert old in text
    return reader(text.replace(old, new, 1))


def refused(read_asset, code="invalid_field"):
    """Return the message of review_policy's refusal, which carries code."""
    with pytest.raises(GatesealError) as raised:
        review_policy(read_asset)
    assert raised.value.code == code
    return str(raised.value)


def test_a_policy_records_the_compiler_it_requires():
    edited = assets('minimum_compiler = "0.1.0"', 'minimum_compiler = "0.0.9"')
    assert review_policy(edited).minimum_compiler == "0.0.9"
    assert load_policy(edited).minimum_compiler == "0.0.9"


def test_a_release_label_holds_at_most_128_printable_characters():
    label = "r" * 128
    review = review_policy(
        assets('release = "2026.11-team-tools"', f'release = "{label}"')
    )
    assert review.release == label
    for release in ("r" * 129, "2026.11\\u007f"):
        edited = assets('release = "2026.11-team-tools"', f'release = "{release}"')
        assert "Policy release" in refused(edited)


def test_a_version_key_must_be_a_stable_release():
    edited = assets(HOOKS_5, '[components.pre-commit-hooks.versions."5.0"]\n')
    assert "components.pre-commit-hooks.versions.5.0" in refused(edited).split()


def test_an_unpinned_version_must_require_a_stable_release():
    edited = assets(HOOKS_5, HOOKS_5 + 'requires = { ruff = "latest" }\n')
    field = "components.pre-commit-hooks.versions.5.0.0.requires.ruff"
    assert field in refused(edited).split()


def test_a_version_may_require_a_built_in_tool_the_policy_does_not_offer():
    text = BUNDLED["policy.toml"].decode()
    for table in (
        '[capabilities.secrets]\ntools = ["betterleaks"]\n\n',
        '[tools.betterleaks]\nconfiguration = "rules/betterleaks.toml"\n\n',
    ):
        text = text.replace(table, "")
    text = text.split("[components.betterleaks]")[0].replace(
        RUFF, RUFF + 'requires = { betterleaks = "1.9.0" }\n'
    )
    review = review_policy(reader(text))
    assert review.capabilities == ("hygiene", "python")
    assert review.catalogs["ruff"].requires == {"0.17.1": {"betterleaks": "1.9.0"}}
    unknown = text.replace('{ betterleaks = "1.9.0" }', '{ black = "1.0.0" }')
    message = refused(reader(unknown))
    assert "components.ruff.versions.0.17.1.requires" in message.split()
    assert "'black'" in message


@pytest.mark.parametrize(
    ("table", "name"),
    [
        ("[capabilities.secrets]\ntools = []\n", "secrets"),
        ('[capabilities.Secrets]\ntools = ["betterleaks"]\n', "Secrets"),
    ],
)
def test_a_capability_needs_a_lowercase_name_and_a_tool(table, name):
    edited = assets('[capabilities.secrets]\ntools = ["betterleaks"]\n', table)
    assert f"capabilities.{name}" in refused(edited).split()


def test_a_pin_may_require_exactly_another_pin():
    exact = assets(HOOKS_6, HOOKS_6 + 'requires = { ruff = "0.17.1" }\n')
    assert review_policy(exact).catalogs["pre-commit-hooks"].requires["6.0.0"] == {
        "ruff": "0.17.1"
    }
    newer = assets(HOOKS_6, HOOKS_6 + 'requires = { ruff = "0.17.2" }\n')
    message = refused(newer, "incompatible_policy")
    named = ("pre-commit-hooks 6.0.0", "ruff 0.17.2", "ruff 0.17.1")
    assert all(value in message for value in named)


def test_an_executable_policy_file_is_refused(tmp_path):
    for name, content in BUNDLED.items():
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_bytes(content)
        (tmp_path / name).chmod(0o644)
    assert load_policy_tree(tmp_path).release == "2026.11-team-tools"
    (tmp_path / "rules" / "ruff.toml").chmod(0o755)
    with pytest.raises(GatesealError) as raised:
        load_policy_tree(tmp_path)
    assert raised.value.code == "file_mode_mismatch"
    assert all(value in str(raised.value) for value in ("rules/ruff.toml", "0755"))
