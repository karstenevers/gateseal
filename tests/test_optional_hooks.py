"""A Policy Author marks hooks as optional, and a repository skips them.

Each test edits the bundled policy.toml in memory, as test_policy_review does,
and reviews it or compiles a Manifest against it in process. The optional and
skip tables come from the policy format page and the Manifest reference.
"""

from datetime import date

import pytest
from conftest import PIN
from doc_examples import example
from gateseal.diagnostics import GatesealError
from gateseal.lock import compile_lock, compile_outputs
from gateseal.manifest import BundledSource, notes, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy, load_policy
from gateseal.policy_tools import OptionalHooks
from test_policy_review import SECRETS, assets, refused

HYGIENE = "[capabilities.hygiene.choices]\nexclude = []\n"
OPTIONAL = example("policy-format.md", "policy tables: optional")
SKIP_CHECK_YAML = "\n" + example("policy-contract.md", "manifest tables: skip")
SECRETS_OPTIONAL = (
    SECRETS + '\n[capabilities.secrets]\noptional = ["betterleaks"]\n\n'
    "[capabilities.secrets.choices]\nskip = []\n"
)
CONFIG = ".pre-commit-config.yaml"


def policy(old=HYGIENE, new=OPTIONAL):
    return load_policy(assets(old, new))


def selection(chosen, extra="", capabilities=("hygiene", "secrets")):
    text = render(capabilities, BundledSource(chosen.release)).decode() + extra
    return resolve(parse_manifest(text.encode()), chosen)


def skip(capability, *hooks):
    listed = ", ".join(f'"{hook}"' for hook in hooks)
    return f"\n[choices.{capability}]\nskip = [{listed}]\n"


def test_a_skipped_hook_leaves_the_configuration():
    optional = policy()
    kept = selection(optional)
    skipped = selection(optional, SKIP_CHECK_YAML)
    config = compile_outputs(skipped, PIN, BundledSource(optional.release))[CONFIG]
    assert b'"check-yaml"' not in config
    assert (
        b'"check-yaml"'
        in compile_outputs(kept, PIN, BundledSource(optional.release))[CONFIG]
    )
    assert [hook.id for hook in skipped.hooks] == [
        "trailing-whitespace",
        "end-of-file-fixer",
        "betterleaks",
    ]
    assert skipped.choices["hygiene"] == {"exclude": (), "skip": ("check-yaml",)}
    assert [note.message for note in notes(skipped, date(2026, 11, 16))] == [
        "Choice hygiene.exclude: nothing.",
        "Choice hygiene.skip: check-yaml.",
    ]
    assert "Choice hygiene.skip: nothing." in {
        note.message for note in notes(kept, date(2026, 11, 16))
    }


def test_a_tool_left_without_hooks_is_not_used():
    optional = policy(SECRETS, SECRETS_OPTIONAL)
    source = BundledSource(optional.release)
    skipped = selection(optional, skip("secrets", "betterleaks"))
    assert [tool.definition.name for tool in skipped.tools] == ["pre-commit-hooks"]
    manifest = (
        render(("hygiene", "secrets"), source) + skip("secrets", "betterleaks").encode()
    )
    lock = compile_lock(manifest, source, optional, PIN, skipped)
    assert lock["native_artifacts"] == {}
    assert sorted(lock["outputs"]) == [CONFIG]
    exception = (
        '\n[exceptions.betterleaks]\nversion = "1.8.0"\nreason = "r"\n'
        "review_by = 2026-12-31\n"
    )
    with pytest.raises(GatesealError) as raised:
        selection(optional, skip("secrets", "betterleaks") + exception)
    assert raised.value.code == "unused_exception"


@pytest.mark.parametrize(
    ("extra", "code", "message"),
    [
        (
            skip("hygiene", "end-of-file-fixer"),
            "invalid_choice",
            "choices.hygiene.skip names end-of-file-fixer, which the policy does "
            "not mark as optional. It may skip check-yaml.",
        ),
        (
            skip("hygiene", "check-yaml", "check-yaml"),
            "invalid_choice",
            "choices.hygiene.skip names a hook twice.",
        ),
        (
            skip("secrets", "betterleaks"),
            "unsupported_choice",
            "The policy exposes no choice secrets.skip. It marks no hook of secrets "
            "as optional, so gateseal.toml cannot skip betterleaks.",
        ),
        (
            '\n[choices.secrets]\nexclude = ["vendor"]\n',
            "unsupported_choice",
            "The policy exposes no choice secrets.exclude.",
        ),
    ],
    ids=["not optional", "twice", "not exposed", "exclude not exposed"],
)
def test_a_manifest_skips_only_what_the_policy_marks_optional(extra, code, message):
    with pytest.raises(GatesealError) as raised:
        selection(policy(), extra)
    assert (raised.value.code, str(raised.value)) == (code, message)


@pytest.mark.parametrize(
    ("new", "code", "message"),
    [
        (
            OPTIONAL.replace('"check-yaml"', '"ruff-check"'),
            "unsupported_hook",
            "capabilities.hygiene.optional offers ruff-check, which no tool of "
            "hygiene runs.",
        ),
        (
            OPTIONAL.replace('"check-yaml"', '"check-yaml", "check-yaml"'),
            "invalid_field",
            "capabilities.hygiene.optional needs one or more distinct hook IDs.",
        ),
        (
            OPTIONAL.replace("skip = []\n", ""),
            "invalid_field",
            "capabilities.hygiene.optional needs capabilities.hygiene.choices.skip "
            "with its default.",
        ),
        (
            HYGIENE + "skip = []\n",
            "unsupported_choice",
            "gateseal defines no choice hygiene.skip for this policy's capabilities.",
        ),
    ],
    ids=["hook of another capability", "twice", "no default", "no optional hooks"],
)
def test_a_policy_marks_hooks_that_its_capability_runs(new, code, message):
    assert refused(assets(HYGIENE, new), code) == message


def test_an_alternative_cannot_also_be_optional():
    edited = assets(
        "[capabilities.python.choices]",
        '[capabilities.python]\noptional = ["ruff-format"]\n\n'
        '[capabilities.python.alternatives]\nformatter = ["ruff-format", "ruff-check"]'
        "\n\n[capabilities.python.choices]",
    )
    assert refused(edited, "duplicate_identifier") == (
        "capabilities.python.optional offers ruff-format, which "
        "capabilities.python.alternatives.formatter offers too."
    )


def test_skip_is_a_name_that_gateseal_reserves():
    edited = assets(
        "[capabilities.python.choices]",
        '[capabilities.python.alternatives]\nskip = ["ruff-check", "ruff-format"]'
        "\n\n[capabilities.python.choices]",
    )
    assert refused(edited) == (
        "capabilities.python.alternatives.skip declares the choice skip, which "
        "gateseal itself declares."
    )


def test_the_bundled_policy_marks_no_hook_optional():
    bundled = load_bundled_policy()
    assert not any(
        isinstance(choice, OptionalHooks) for choice in bundled.hook_choices.values()
    )
