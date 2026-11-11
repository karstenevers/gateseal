"""A Repository Maintainer's gateseal.toml is parsed and resolved exactly.

Each test renders a Manifest for the bundled policy, or one that also pins a
Hook Python, and parses, resolves or describes it in process.
"""

import shutil
from datetime import date

import pytest
from black_policy import add_black
from gateseal.diagnostics import GatesealError
from gateseal.manifest import (
    REASON_LIMIT,
    BundledSource,
    capabilities,
    git_source,
    notes,
    parse_manifest,
    render,
    resolve,
)
from gateseal.policy import bundled_root, load_bundled_policy, load_policy_tree

POLICY = load_bundled_policy()
MANIFEST = render(("hygiene", "secrets"), BundledSource(POLICY.release)).decode()


def exception(reason='"Upstream bug"', review_by="2026-12-31"):
    return (
        '\n[exceptions.betterleaks]\nversion = "1.8.0"\n'
        f"reason = {reason}\nreview_by = {review_by}\n"
    )


def refused(text):
    with pytest.raises(GatesealError) as raised:
        parse_manifest(text.encode())
    return raised.value.code


@pytest.mark.parametrize(
    "names",
    [["hygiene", "hygiene"], ["hygiene", "Python"], ["python"], ["secrets", "python"]],
)
def test_a_selection_names_hygiene_and_each_capability_once(names):
    with pytest.raises(GatesealError) as raised:
        capabilities(names)
    assert raised.value.code == "unsupported_capability"
    assert "hygiene" in str(raised.value)


def test_a_selection_is_sorted_by_name():
    assert capabilities(["secrets", "hygiene", "python"]) == (
        "hygiene",
        "python",
        "secrets",
    )


@pytest.mark.parametrize(
    ("schema", "code"), [("2", "unsupported_schema"), ('"1"', "invalid_field")]
)
def test_a_manifest_names_schema_1(schema, code):
    assert refused(MANIFEST.replace("schema = 1", f"schema = {schema}")) == code


def test_a_reason_holds_at_most_the_limit():
    longest = "r" * REASON_LIMIT
    (granted,) = parse_manifest(
        (MANIFEST + exception(f'"{longest}"')).encode()
    ).exceptions
    assert granted.reason == longest
    assert refused(MANIFEST + exception(f'"{longest}r"')) == "invalid_exception"


@pytest.mark.parametrize(
    ("source", "line"),
    [
        (BundledSource("2026.11-équipe"), 'release = "2026.11-équipe"'),
        (
            git_source(
                "https://github.com/team/policy.git",
                "règles",
                "0123456789abcdef0123456789abcdef01234567",
                "v1",
            ),
            'path = "règles"',
        ),
    ],
)
def test_a_rendered_policy_table_keeps_its_text(source, line):
    rendered = render(("hygiene",), source).decode()
    assert line in rendered.splitlines()
    assert parse_manifest(rendered.encode()).source == source


def test_an_exception_due_today_is_not_overdue():
    manifest = parse_manifest((MANIFEST + exception(review_by="2026-10-27")).encode())
    found = notes(resolve(manifest, POLICY), date(2026, 10, 27))
    assert [(note.severity, note.category) for note in found] == [
        ("info", "policy_choice"),
        ("info", "version_exception"),
    ]
    later = notes(resolve(manifest, POLICY), date(2026, 10, 28))
    assert later[1].category == "overdue_exception"


def test_a_hook_python_runs_hooks_beside_a_tool_that_needs_no_python(tmp_path):
    root = tmp_path / "policy"
    shutil.copytree(bundled_root(), root, ignore=shutil.ignore_patterns("*.md"))
    add_black(root, python=True, locked=True)
    policy = load_policy_tree(root)
    selected = render(("hygiene", "secrets"), BundledSource(policy.release))
    selection = resolve(parse_manifest(selected), policy)
    assert selection.python == policy.hook_python
    assert selection.python.version == "3.15.2"
