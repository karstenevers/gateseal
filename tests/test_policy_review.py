"""A policy's authors get each mistake in policy.toml named before a release.

Each test edits the bundled policy.toml in memory and reviews or loads it, as
gateseal policy check does for a policy's files.
"""

import pytest
from conftest import BUNDLED_MINIMUM, BUNDLED_TOOLS, newer_release, reviewed_line
from gateseal import __version__
from gateseal.diagnostics import GatesealError
from gateseal.policy import (
    load_bundled_policy,
    load_policy,
    load_policy_tree,
    review_policy,
)

BUNDLED = {asset.path: asset.content for asset in load_bundled_policy().assets}
HOOKS_PIN = 'pin = "6.0.0"\n'
HOOKS = 'reviewed = ["6.0.0", "5.0.0"]\n'
RUFF_PIN = BUNDLED_TOOLS["ruff"].pin
RUFF = reviewed_line("ruff") + "\n"
SECRETS_PIN = f'pin = "{BUNDLED_TOOLS["betterleaks"].pin}"\n'
SECRETS = (
    "[capabilities.secrets.tools.betterleaks]\n"
    'configuration = "rules/betterleaks.toml"\n'
    f"{SECRETS_PIN}{reviewed_line('betterleaks')}\n"
)


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
    edited = assets(BUNDLED_MINIMUM, 'minimum_compiler = "0.0.9"')
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


def test_a_reviewed_version_must_be_a_stable_release():
    edited = assets(HOOKS, 'reviewed = ["6.0.0", "5.0"]\n')
    field = "capabilities.hygiene.tools.pre-commit-hooks.reviewed"
    assert field in refused(edited).split()


def test_a_requirement_must_name_a_stable_release():
    edited = assets(HOOKS, HOOKS + 'requires."5.0.0" = { ruff = "latest" }\n')
    field = "capabilities.hygiene.tools.pre-commit-hooks.requires.5.0.0.ruff"
    assert field in refused(edited).split()


def test_a_version_may_require_a_built_in_tool_the_policy_does_not_offer():
    text = BUNDLED["policy.toml"].decode().replace("\n" + SECRETS, "")
    text = text.replace(RUFF, RUFF + 'requires."0.17.1" = { betterleaks = "1.9.0" }\n')
    review = review_policy(reader(text))
    assert review.capabilities == (
        "hygiene",
        "python",
        "python-tests",
        "python-crap",
        "python-mutation",
        "python-types",
    )
    unrequired = dict.fromkeys(BUNDLED_TOOLS["ruff"].versions, {})
    assert review.catalogs["ruff"].requires == unrequired | {
        "0.17.1": {"betterleaks": "1.9.0"}
    }
    unknown = text.replace('{ betterleaks = "1.9.0" }', '{ black = "1.0.0" }')
    message = refused(reader(unknown))
    assert "capabilities.python.tools.ruff.requires.0.17.1" in message.split()
    assert "'black'" in message


@pytest.mark.parametrize(
    ("table", "name"),
    [
        ("[capabilities.secrets]\ntools = {}\n", "secrets"),
        (SECRETS.replace("secrets", "Secrets"), "Secrets"),
    ],
)
def test_a_capability_needs_a_lowercase_name_and_a_tool(table, name):
    edited = assets(SECRETS, table)
    assert f"capabilities.{name}" in refused(edited).split()


def test_a_pin_may_require_exactly_another_pin():
    exact = assets(
        HOOKS_PIN, HOOKS_PIN + f'requires."6.0.0" = {{ ruff = "{RUFF_PIN}" }}\n'
    )
    assert review_policy(exact).catalogs["pre-commit-hooks"].requires["6.0.0"] == {
        "ruff": RUFF_PIN
    }
    newer_ruff = newer_release("ruff")
    newer = assets(
        HOOKS_PIN, HOOKS_PIN + f'requires."6.0.0" = {{ ruff = "{newer_ruff}" }}\n'
    )
    message = refused(newer, "incompatible_policy")
    named = ("pre-commit-hooks 6.0.0", f"ruff {newer_ruff}", f"ruff {RUFF_PIN}")
    assert all(value in message for value in named)


@pytest.mark.parametrize("table", ["tools", "components", "choices"])
def test_a_top_level_table_of_another_layout_is_named(table):
    edited = assets(
        "\n[capabilities.hygiene", f"\n[{table}.ruff]\n\n[capabilities.hygiene"
    )
    assert refused(edited) == (
        f"policy.toml has {table} at its top level, which holds only schema, "
        "release, minimum_compiler, capabilities and the optional "
        "time_budget_seconds."
    )


def test_two_capabilities_cannot_hold_one_tool():
    edited = assets(SECRETS, SECRETS + "\n" + SECRETS.replace("secrets.", "leaks."))
    assert refused(edited, "duplicate_identifier") == (
        "capabilities.secrets and capabilities.leaks both have the tool "
        "betterleaks; a tool belongs to exactly one capability."
    )


def test_a_pin_is_one_of_the_reviewed_versions():
    newer = newer_release("betterleaks")
    edited = assets(SECRETS_PIN, f'pin = "{newer}"\n')
    assert refused(edited) == (
        f"capabilities.secrets.tools.betterleaks.pin {newer} is not in "
        "capabilities.secrets.tools.betterleaks.reviewed."
    )


def test_a_requirement_belongs_to_a_reviewed_version():
    newer = newer_release("ruff")
    edited = assets(RUFF, RUFF + f'requires."{newer}" = {{ betterleaks = "1.9.0" }}\n')
    assert refused(edited) == (
        f"capabilities.python.tools.ruff.requires.{newer} names {newer}, which "
        "capabilities.python.tools.ruff.reviewed does not list."
    )


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


def test_a_capability_without_a_history_scan_may_expose_exclude():
    edited = assets('target_version = "py311"\n', 'exclude = ["vendor"]\n')
    assert review_policy(edited).choices == {
        ("hygiene", "exclude"): (),
        ("python", "exclude"): ("vendor",),
        ("python-crap", "exclude"): (),
    }


def test_a_capability_with_a_history_scan_cannot_expose_exclude():
    edited = assets(
        SECRETS, SECRETS + "\n[capabilities.secrets.choices]\nexclude = []\n"
    )
    with pytest.raises(GatesealError) as raised:
        review_policy(edited)
    assert (raised.value.code, str(raised.value), raised.value.next_step) == (
        "invalid_field",
        "capabilities.secrets.choices exposes exclude, but betterleaks in secrets "
        "scans history, which reads every path.",
        "Move betterleaks to a capability that exposes no exclude, or stop "
        "exposing exclude in secrets.",
    )


def test_alternatives_cannot_be_named_exclude():
    edited = assets(
        "[capabilities.python.choices]",
        '[capabilities.python.alternatives]\nexclude = ["ruff-check", "ruff-format"]'
        "\n\n[capabilities.python.choices]",
    )
    assert refused(edited) == (
        "capabilities.python.alternatives.exclude declares the choice exclude, "
        "which gateseal itself declares."
    )


def test_a_policy_in_an_older_schema_stops_before_its_layout_is_read():
    text = BUNDLED["policy.toml"].decode().replace("schema = 5", "schema = 4")
    with pytest.raises(GatesealError) as raised:
        review_policy(reader(text + '\n[components.ruff]\nreviewed = "0.17.1"\n'))
    assert (raised.value.code, str(raised.value)) == (
        "unsupported_schema",
        f"The policy uses schema 4; gateseal {__version__} reads policy schema 5.",
    )


@pytest.mark.parametrize(
    ("alternatives", "code", "message"),
    [
        (
            'formatter = ["ruff-format", "check-yaml"]',
            "unsupported_hook",
            "capabilities.python.alternatives.formatter offers check-yaml, which no "
            "tool of python runs.",
        ),
        (
            'formatter = ["ruff-format", "ruff-check"]\n'
            'linter = ["ruff-check", "ruff-format"]',
            "duplicate_identifier",
            "capabilities.python.alternatives.linter offers ruff-check, which "
            "capabilities.python.alternatives.formatter offers too.",
        ),
    ],
    ids=["hook of another capability", "hook in two choices"],
)
def test_an_alternative_is_a_hook_of_its_capability_in_one_choice(
    alternatives, code, message
):
    edited = assets(
        "[capabilities.python.choices]",
        f"[capabilities.python.alternatives]\n{alternatives}\n\n"
        "[capabilities.python.choices]",
    )
    assert refused(edited, code) == message
