"""The Manifest's [ci] table is parsed exactly, and each invalid value names its key."""

import pytest
from gate_files import BRANCH, manifest
from gateseal.ci_providers import PROVIDERS
from gateseal.ci_providers.model import Triggers
from gateseal.ci_providers.settings import CI
from gateseal.diagnostics import GatesealError
from gateseal.manifest import parse_manifest

EACH = pytest.mark.parametrize("provider", sorted(PROVIDERS))


def parsed(provider, keys, branch=BRANCH):
    return parse_manifest(manifest(provider, branch, keys)).ci


def refusal(provider, keys, branch=BRANCH):
    with pytest.raises(GatesealError) as failure:
        parsed(provider, keys, branch)
    assert failure.value.code == "invalid_field"
    return str(failure.value)


@EACH
def test_a_table_without_the_optional_keys_takes_their_defaults(provider):
    assert parsed(provider, "") == CI(provider, Triggers(BRANCH, (), False), 30, ())


def test_a_table_with_every_key_keeps_each_value():
    keys = (
        'branches = ["release/*", "develop"]\nschedule = true\nstandalone = true\n'
        'timeout_minutes = 60\nruns_on = ["self-hosted", "linux"]\n'
    )
    assert parsed("github", keys) == CI(
        "github",
        Triggers(BRANCH, ("release/*", "develop"), True),
        60,
        ("self-hosted", "linux"),
    )
    assert parsed("azure", 'pool = "linux-agents"\n').runner == ("linux-agents",)


@EACH
def test_standalone_false_has_no_triggers(provider):
    keys = "standalone = false\ntimeout_minutes = 45\n"
    assert parsed(provider, keys, branch=None) == CI(provider, None, 45, ())


INVALID = [
    ('branches = "develop"', "branches"),
    ("branches = [1]", "branches"),
    ('branches = ["a..b"]', "branches"),
    ('branches = ["main branch"]', "branches"),
    ('branches = ["release/"]', "branches"),
    ('branches = ["/*"]', "branches"),
    ('branches = ["a..b/*"]', "branches"),
    ('branches = ["release/.draft"]', "branches"),
    ('branches = ["release."]', "branches"),
    ('branches = ["release/team.lock/*"]', "branches"),
    ('branches = ["release//fix"]', "branches"),
    ('branches = ["*"]', "branches"),
    ('branches = ["release*"]', "branches"),
    ('branches = ["release/**"]', "branches"),
    ('branches = ["*/fix"]', "branches"),
    ('branches = ["release/*/fix"]', "branches"),
    ('branches = ["develop", "develop"]', "branches"),
    ('branches = ["release/*", "develop", "release/*"]', "branches"),
    (f'branches = ["{BRANCH}"]', "branches"),
    ('schedule = "true"', "schedule"),
    ("schedule = 1", "schedule"),
    ('standalone = "false"', "standalone"),
    ("standalone = 0", "standalone"),
    ("timeout_minutes = 0", "timeout_minutes"),
    ("timeout_minutes = 361", "timeout_minutes"),
    ("timeout_minutes = -30", "timeout_minutes"),
    ("timeout_minutes = 30.0", "timeout_minutes"),
    ('timeout_minutes = "30"', "timeout_minutes"),
    ("timeout_minutes = true", "timeout_minutes"),
]


@EACH
@pytest.mark.parametrize("keys,key", INVALID)
def test_each_invalid_value_names_its_key(provider, keys, key):
    assert f"ci.{key}" in refusal(provider, keys + "\n")


@pytest.mark.parametrize(
    "keys",
    ['pool = ""', 'pool = ["linux-agents"]', "pool = 1"],
)
def test_a_pool_is_a_nonempty_name(keys):
    assert "ci.pool" in refusal("azure", keys + "\n")


@pytest.mark.parametrize(
    "keys",
    ["runs_on = []", 'runs_on = [""]', 'runs_on = "self-hosted"', "runs_on = [1]"],
)
def test_runs_on_lists_nonempty_labels(keys):
    assert "ci.runs_on" in refusal("github", keys + "\n")


@EACH
@pytest.mark.parametrize(
    "keys,key",
    [
        ('default_branch = "main"', "default_branch"),
        ("branches = []", "branches"),
        ("schedule = false", "schedule"),
    ],
)
def test_standalone_false_refuses_each_trigger_key(provider, keys, key):
    message = refusal(provider, f"standalone = false\n{keys}\n", branch=None)
    assert f"ci.{key}" in message


@EACH
@pytest.mark.parametrize("keys", ["", "standalone = true\n"])
def test_a_pipeline_of_its_own_requires_default_branch(provider, keys):
    assert "default_branch" in refusal(provider, keys, branch=None)


@pytest.mark.parametrize(
    "provider,given,own",
    [
        ("github", "extra = 1", "runs_on"),
        ("github", 'pool = "linux-agents"', "runs_on"),
        ("azure", "extra = 1", "pool"),
        ("azure", 'runs_on = ["x"]', "pool"),
    ],
)
def test_an_unknown_key_is_refused_with_the_keys_the_provider_takes(
    provider, given, own
):
    """That covers the runner key of another provider."""
    message = refusal(provider, given + "\n")
    assert message.startswith(f"ci.{given.split()[0]} is not a key")
    assert message.endswith(
        ", ".join(
            sorted(
                {"branches", "default_branch", "provider", "schedule"}
                | {"standalone", "timeout_minutes", own}
            )
        )
        + "."
    )


def test_a_table_without_a_provider_is_refused():
    text = manifest("github").replace(b'provider = "github"\n', b"")
    with pytest.raises(GatesealError) as failure:
        parse_manifest(text)
    assert failure.value.code == "invalid_field"
    assert "provider" in str(failure.value)
