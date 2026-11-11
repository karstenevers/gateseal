from datetime import date

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.manifest import BundledSource, ExceptionRequest, GitSource
from gateseal.manifest_toml import edited

MANIFEST = """\
# Our checks.
schema = 1
capabilities = ["hygiene", "secrets"]

# Team policy, adopted in October.
[policy]
source = "git"
repository = "https://policy.example/team/gateseal-policy.git"
path = "policy"
commit = "1111111111111111111111111111111111111111"
tag = "v1"

[choices.hygiene]
exclude = ["vendor"]  # generated

[exceptions]

# Fixtures trip 1.9.0.
[exceptions.betterleaks]
version = "1.8.0"
reason = "1.9.0 flags our fixtures"
review_by = 2026-12-15
"""

HOOKS = ExceptionRequest(
    "pre-commit-hooks", "5.0.0", "6.0.0 rejects our YAML", date(2027, 1, 31)
)


def test_policy_target_replaces_only_the_policy_table():
    source = GitSource(
        "https://policy.example/team/gateseal-policy.git",
        "policy",
        "2222222222222222222222222222222222222222",
        "v2",
    )
    result = edited(MANIFEST.encode(), source, {}).decode()
    assert result == MANIFEST.replace("1" * 40, "2" * 40).replace(
        'tag = "v1"', 'tag = "v2"'
    )


def test_bundled_target_rewrites_the_policy_keys():
    result = edited(MANIFEST.encode(), BundledSource("2026.10"), {}).decode()
    assert (
        '# Team policy, adopted in October.\n[policy]\nsource = "bundled"\n' in result
    )
    assert 'release = "2026.10"\n\n[choices.hygiene]' in result
    assert "repository" not in result


def test_exceptions_are_added_changed_and_removed_with_their_comments():
    renewed = ExceptionRequest("betterleaks", "1.8.0", "Still flags", date(2027, 3, 1))
    result = edited(
        MANIFEST.encode(), None, {"betterleaks": renewed, "pre-commit-hooks": HOOKS}
    ).decode()
    assert result.endswith(
        '# Fixtures trip 1.9.0.\n[exceptions.betterleaks]\nversion = "1.8.0"\n'
        'reason = "Still flags"\nreview_by = 2027-03-01\n\n'
        '[exceptions.pre-commit-hooks]\nversion = "5.0.0"\n'
        'reason = "6.0.0 rejects our YAML"\nreview_by = 2027-01-31\n'
    )
    removed = edited(result.encode(), None, {"betterleaks": None}).decode()
    assert "Fixtures" not in removed
    assert removed.endswith(
        '[exceptions]\n\n[exceptions.pre-commit-hooks]\nversion = "5.0.0"\n'
        'reason = "6.0.0 rejects our YAML"\nreview_by = 2027-01-31\n'
    )
    assert edited(removed.encode(), None, {"pre-commit-hooks": None}).decode() == (
        MANIFEST.split("\n# Fixtures")[0] + "\n"
    ).replace("[exceptions]\n\n", "[exceptions]\n")


@pytest.mark.parametrize(
    "manifest",
    [
        MANIFEST.replace(
            '[exceptions.betterleaks]\nversion = "1.8.0"\nreason = "1.9.0 flags our '
            'fixtures"\nreview_by = 2026-12-15\n',
            'betterleaks = { version = "1.8.0", reason = "r", '
            "review_by = 2026-12-15 }\n",
        ),
        MANIFEST.split("# Team")[0]
        + 'policy = { source = "bundled", release = "2026.09" }\n'
        + MANIFEST.split('tag = "v1"\n')[1],
    ],
)
def test_a_manifest_it_cannot_edit_is_refused(manifest):
    with pytest.raises(GatesealError) as raised:
        edited(manifest.encode(), BundledSource("2026.10"), {"betterleaks": None})
    assert raised.value.code == "manifest_edit_unsupported"


def test_removing_the_last_exception_keeps_an_empty_exceptions_table():
    implicit = MANIFEST.replace("[exceptions]\n\n", "")
    result = edited(implicit.encode(), None, {"betterleaks": None}).decode()
    assert result.endswith('exclude = ["vendor"]  # generated\n\n[exceptions]\n')
