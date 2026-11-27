"""The Tool Definition fields that every kind shares, checked as one is parsed.

Each test edits a small team-defined release binary and loads it as gateseal
policy lock does, then checks the exact refusal.
"""

import json

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.tools import built_in_tools, load_definition
from gateseal.tools.model import (
    AT_COMMIT,
    Origin,
    Scope,
    changed_upstream,
    local_repository,
    require_local_hooks,
)

CONTENT = 'content = "{{ range . }}{{ .File }}\\n{{ end }}"'
TOOL = f"""\
kind = "release-binary"
stages = ["commit", "run", "ci"]
scope = "files"

[source]
repository = "https://github.com/gitleaks/gitleaks"
tag = "v{{version}}"
archive = "gitleaks_{{version}}_{{platform}}.tar.gz"
checksums = "gitleaks_{{version}}_checksums.txt"

[source.platforms]
linux-x64 = "linux_x64"

[executable]
name = "gitleaks"

[files.report]
extension = "tmpl"
{CONTENT}

[hooks.gitleaks]
name = "Detect secrets with Gitleaks"
args = ["dir", "--report-template", "{{report}}"]
output = "concise"
"""


def refusal(old, new):
    assert old in TOOL
    with pytest.raises(GatesealError) as failure:
        load_definition("gitleaks", TOOL.replace(old, new, 1).encode(), Origin.TEAM)
    assert failure.value.code == "invalid_field"
    return str(failure.value)


def test_a_definition_resolves_each_fixed_file_to_its_owned_path():
    definition = load_definition("gitleaks", TOOL.encode(), Origin.TEAM)
    assert definition.files == {
        ".gateseal/gitleaks-report.tmpl": b"{{ range . }}{{ .File }}\n{{ end }}"
    }
    assert definition.hooks["gitleaks"].args == (
        "dir",
        "--report-template",
        ".gateseal/gitleaks-report.tmpl",
    )


@pytest.mark.parametrize(
    "old,new,key",
    [
        ("[files.report]", "[files.Report]", "Report"),
        ("[files.report]", "[files.configuration]", "configuration"),
        ('extension = "tmpl"', 'extension = "TMPL"', "report"),
        (CONTENT, "content = 5", "report"),
        (".File }}\\n{{", ".File }}\\r\\n{{", "report"),
    ],
    ids=["uppercase key", "configuration key", "extension", "not text", "CRLF"],
)
def test_a_fixed_file_has_a_lowercase_name_and_lf_text(old, new, key):
    assert f"tools.gitleaks.files.{key}" in refusal(old, new).split()


def test_a_hook_has_a_lowercase_id():
    assert (
        "tools.gitleaks.hooks.Gitleaks"
        in refusal("[hooks.gitleaks]", "[hooks.Gitleaks]").split()
    )


FINDINGS = 'output = "concise"\n'


def test_a_hook_reads_the_exit_codes_of_its_findings():
    edited = TOOL.replace(FINDINGS, FINDINGS + "finding_exit_codes = [1, 255]\n")
    definition = load_definition("gitleaks", edited.encode(), Origin.TEAM)
    assert definition.hooks["gitleaks"].finding_exit_codes == {1, 255}
    plain = load_definition("gitleaks", TOOL.encode(), Origin.TEAM)
    assert plain.hooks["gitleaks"].finding_exit_codes == frozenset()


@pytest.mark.parametrize(
    "codes",
    ["[]", "[0]", "[256]", "[-1]", "[1, 1]", '["1"]', "[true]", "[1.0]", "1"],
    ids=[
        "empty",
        "zero",
        "too high",
        "negative",
        "twice",
        "text",
        "boolean",
        "float",
        "not a list",
    ],
)
def test_finding_exit_codes_are_one_or_more_codes_from_1_to_255(codes):
    message = refusal(FINDINGS, f"{FINDINGS}finding_exit_codes = {codes}\n")
    assert "tools.gitleaks.hooks.gitleaks.finding_exit_codes" in message.split()


EXCLUDED = FINDINGS + "exclude = '(^|/)tests?/'\n"


def test_a_hook_never_gets_a_file_that_its_exclude_matches():
    """The hook's own pattern adds to the paths that its capability keeps from it."""
    definition = load_definition(
        "gitleaks", TOOL.replace(FINDINGS, EXCLUDED).encode(), Origin.TEAM
    )
    hook = definition.hooks["gitleaks"]
    assert (
        hook.base_entry(None, AT_COMMIT, Scope.FILES)["exclude"] == "(?:(^|/)tests?/)"
    )
    paths = "^(?:vendor(?:/|$))"
    rendered = local_repository(("gitleaks",), [hook], paths, AT_COMMIT, Scope.FILES)
    (entry,) = rendered["hooks"]
    assert entry["exclude"] == f"{paths}|(?:(^|/)tests?/)"
    plain = load_definition("gitleaks", TOOL.encode(), Origin.TEAM).hooks["gitleaks"]
    assert "exclude" not in plain.base_entry(None, AT_COMMIT, Scope.FILES)
    assert plain.base_entry(paths, AT_COMMIT, Scope.FILES)["exclude"] == paths


@pytest.mark.parametrize(
    "pattern",
    ["'('", "5", "['a']", "'(?i)a'"],
    ids=["open", "number", "list", "flag for the whole"],
)
def test_a_hook_s_exclude_is_a_regular_expression(pattern):
    message = refusal(FINDINGS, f"{FINDINGS}exclude = {pattern}\n")
    assert "tools.gitleaks.hooks.gitleaks.exclude" in message.split()


@pytest.mark.parametrize(
    "old,new,named",
    [
        (
            'tag = "v{version}"',
            'tag = "v{version}-{version}"',
            ["tools.gitleaks.source.tag", "{version}"],
        ),
        (
            "gitleaks_{version}_{platform}.tar.gz",
            "gitleaks_{platform}_{platform}.tar.gz",
            ["tools.gitleaks.source.archive", "{platform}"],
        ),
        (
            "gitleaks_{version}_checksums.txt",
            "sums/{version}.txt",
            ["tools.gitleaks.source.checksums"],
        ),
        ('"linux_x64"', '"linux/x64"', ["tools.gitleaks.source.platforms.linux-x64"]),
    ],
    ids=["tag twice", "platform twice", "checksums path", "platform path"],
)
def test_a_release_file_pattern_names_each_placeholder_it_needs_once(old, new, named):
    message = refusal(old, new)
    assert all(value in message for value in named)


def test_an_upstream_change_shows_both_facts_with_sorted_keys():
    source = "gitleaks_8.30.0_checksums.txt of gitleaks 8.30.0"
    recorded = {"url": "https://example.org/a", "sha256": "a" * 64}
    found = {"url": "https://example.org/b", "sha256": "b" * 64}
    failure = changed_upstream("gitleaks", "8.30.0", source, recorded, found)
    assert failure.code == "upstream_changed"
    assert source in str(failure)
    for fact in (recorded, found):
        assert json.dumps(fact, sort_keys=True) in str(failure)
    assert "gateseal policy lock" in failure.next_step


def test_local_hooks_run_on_pre_commit_4_4_0_and_newer():
    hooks = tuple(built_in_tools()["betterleaks"].hooks.values())
    require_local_hooks(hooks, "4.4.0")
    with pytest.raises(GatesealError) as failure:
        require_local_hooks(hooks, "4.3.12")
    assert failure.value.code == "incompatible_runtime"
    assert all(v in str(failure.value) for v in ("betterleaks", "4.4.0", "4.3.12"))
