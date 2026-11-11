import dataclasses
import json
from datetime import date

import pytest
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError, Result
from gateseal.lock import acquire, compile_outputs, exclusion
from gateseal.manifest import (
    BundledSource,
    git_source,
    notes,
    parse_manifest,
    render,
    resolve,
)
from gateseal.policy import load_bundled_policy

POLICY = load_bundled_policy()
COMMIT = "0123456789abcdef0123456789abcdef01234567"
ALL = ("hygiene", "python", "secrets")
(RULES,) = (a.content for a in POLICY.assets if a.path == "rules/ruff.toml")


def manifest(extra="", capabilities=("hygiene", "secrets")):
    text = render(capabilities, BundledSource(POLICY.release)).decode()
    return parse_manifest((text + extra).encode())


def selection(extra="", capabilities=("hygiene", "secrets")):
    return resolve(manifest(extra, capabilities), POLICY)


def outputs(chosen):
    wheel = "gateseal-0.1.0-py3-none-any.whl"
    artifact = CompilerArtifact("local-wheel", f"/{wheel}", wheel, "a" * 64, "0.1.0")
    return compile_outputs(chosen, artifact, BundledSource(POLICY.release))


def test_defaults_select_every_pin():
    chosen = selection()
    assert chosen.choices == {"hygiene": {"exclude": ()}}
    assert [note.message for note in notes(chosen, date(2026, 10, 27))] == [
        "Choice hygiene.exclude: nothing."
    ]
    assert chosen.exceptions == {}
    assert {tool.definition.name: tool.version for tool in chosen.tools} == {
        "betterleaks": "1.9.0",
        "pre-commit-hooks": "6.0.0",
    }


def test_exclude_choice_renders_for_hygiene_hooks_only():
    chosen = selection(
        '\n[choices.hygiene]\nexclude = ["vendor", "docs/gen.d"]\n',
        ("hygiene", "python", "secrets"),
    )
    assert chosen.choices["hygiene"]["exclude"] == ("vendor", "docs/gen.d")
    config = outputs(chosen)[".pre-commit-config.yaml"].decode()
    pattern = exclusion(("vendor", "docs/gen.d"))
    assert pattern == r"^(?:vendor(?:/|$)|docs/gen\.d(?:/|$))"
    assert config.count(pattern.replace("\\", "\\\\")) == 3
    assert '"id":"ruff-check"' in config.replace(" ", "")
    assert '"id":"betterleaks"' in config.replace(" ", "")


def test_python_selects_the_ruff_hook_repository():
    chosen = selection(capabilities=("hygiene", "python", "secrets"))
    components = [t.lock_entry() for t in chosen.tools if t.use.capability != "secrets"]
    assert [(section, key) for section, key, _ in components] == [
        ("components", "pre-commit-hooks"),
        ("components", "ruff"),
    ]
    assert components[1][2] == {
        "repository": "https://github.com/astral-sh/ruff-pre-commit",
        "version": "0.17.1",
        "revision": "38bfb07c0c64c304f47eb52090f8288150c77730",
        "hook_ids": ["ruff-check", "ruff-format"],
        "tools": {"ruff": "0.17.1"},
    }
    assert tuple(hook.id for hook in chosen.hooks) == (
        "trailing-whitespace",
        "end-of-file-fixer",
        "check-yaml",
        "ruff-check",
        "ruff-format",
        "betterleaks",
    )
    assert "python" not in {tool.use.capability for tool in selection().tools}


def target(value):
    return f"\n[choices.python]\ntarget_version = {value}\n"


def test_target_version_renders_first_in_the_ruff_configuration():
    default = selection(capabilities=ALL)
    assert default.choices == {
        "hygiene": {"exclude": ()},
        "python": {"target_version": "py311"},
    }
    assert outputs(default)[".gateseal/ruff.toml"] == (
        b'target-version = "py311"\n' + RULES
    )
    chosen = selection(target('"py39"'), ALL)
    assert outputs(chosen)[".gateseal/ruff.toml"] == (
        b'target-version = "py39"\n' + RULES
    )
    assert [note.message for note in notes(chosen, date(2026, 10, 27))] == [
        "Choice hygiene.exclude: nothing.",
        "Choice python.target_version: py39.",
    ]


@pytest.mark.parametrize(
    "value", ['"py27"', '"3.9"', '"py316"', '"PY39"', "39", '["py39"]']
)
def test_target_version_must_be_one_ruff_accepts(value):
    with pytest.raises(GatesealError) as failure:
        selection(target(value), ALL)
    assert failure.value.code == "invalid_choice"
    assert "py37, py38, py39" in str(failure.value)


def ruff_reviewing(*versions):
    """Return the bundled policy with Ruff reviewing versions beside its pin."""
    tool = POLICY.tools["ruff"]
    reviewed = dict(tool.versions)
    for version in versions:
        reviewed[version] = dataclasses.replace(reviewed["0.17.1"], version=version)
    tools = dict(POLICY.tools, ruff=dataclasses.replace(tool, versions=reviewed))
    return dataclasses.replace(POLICY, tools=tools)


@pytest.mark.parametrize(
    "version,value,accepted",
    [
        ("0.4.4", '"py313"', False),
        ("0.4.5", '"py313"', True),
        ("0.11.7", '"py314"', False),
        ("0.11.8", '"py314"', True),
        ("0.14.10", '"py315"', False),
        ("0.14.11", '"py315"', True),
        ("0.4.4", None, True),
    ],
)
def test_a_version_exception_must_accept_the_target_version(version, value, accepted):
    extra = exception("ruff", version) + (target(value) if value else "")
    policy = ruff_reviewing(version)
    if accepted:
        chosen = resolve(manifest(extra, ALL), policy)
        assert [t.version for t in chosen.tools if t.definition.name == "ruff"] == [
            version
        ]
        return
    with pytest.raises(GatesealError) as failure:
        resolve(manifest(extra, ALL), policy)
    assert failure.value.code == "invalid_choice"
    assert f"which ruff {version} does not accept" in str(failure.value)


@pytest.mark.parametrize(
    "extra,category",
    [
        ('\n[choices.hygiene]\nexclude = ["../outside"]\n', "invalid_choice"),
        ('\n[choices.hygiene]\nexclude = ["vendor/"]\n', "invalid_choice"),
        ('\n[choices.hygiene]\nexclude = ["a b"]\n', "invalid_choice"),
        ('\n[choices.hygiene]\nexclude = ["x", "x"]\n', "invalid_choice"),
        ('\n[choices.hygiene]\nhook_ids = ["check-yaml"]\n', "unsupported_choice"),
        ('\n[choices.secrets]\nrules = "mine.toml"\n', "unsupported_choice"),
        (target('"py39"'), "unused_choice"),
        ("\n[rules]\nallow = true\n", "invalid_field"),
    ],
    ids=[
        "escaping",
        "slash",
        "space",
        "twice",
        "hook ids",
        "rules",
        "python unselected",
        "rule table",
    ],
)
def test_invalid_choices_and_rule_overrides_fail(extra, category):
    with pytest.raises(GatesealError) as failure:
        selection(extra)
    assert failure.value.code == category


def exception(component, version, review_by="2026-12-31", reason='"Upstream bug"'):
    return (
        f"\n[exceptions.{component}]\nversion = {version!r}\n"
        f"reason = {reason}\nreview_by = {review_by}\n"
    ).replace("'", '"')


def test_exception_selects_a_reviewed_version_with_its_reason():
    chosen = selection(exception("betterleaks", "1.8.0"))
    assert [t.version for t in chosen.tools if t.use.capability == "secrets"] == [
        "1.8.0"
    ]
    granted = chosen.exceptions["betterleaks"]
    assert (granted.pin, granted.reason, granted.review_by) == (
        "1.9.0",
        "Upstream bug",
        date(2026, 12, 31),
    )
    config = outputs(selection(exception("pre-commit-hooks", "5.0.0")))
    assert (
        b"cef0300fd0fc4d2a87a85fa2093c6b283ea36f4b" in config[".pre-commit-config.yaml"]
    )


@pytest.mark.parametrize(
    "extra,category",
    [
        (exception("betterleaks", "1.7.0"), "unknown_version"),
        (exception("black", "24.0.0"), "unsupported_component"),
        (exception("betterleaks", "1.8.0", reason='""'), "invalid_exception"),
        (exception("betterleaks", "1.8.0", reason='"   "'), "invalid_exception"),
        (
            exception("betterleaks", "1.8.0", reason='"' + "x" * 501 + '"'),
            "invalid_exception",
        ),
        (
            exception("betterleaks", "1.8.0", review_by='"2026-12-31"'),
            "invalid_exception",
        ),
        (
            exception("betterleaks", "1.8.0", review_by="2026-12-31T10:00:00"),
            "invalid_exception",
        ),
        (
            exception("betterleaks", "1.8.0").replace("version", "rules"),
            "invalid_field",
        ),
    ],
    ids=[
        "unreviewed",
        "unknown component",
        "empty reason",
        "blank reason",
        "long reason",
        "string date",
        "datetime",
        "rule override",
    ],
)
def test_invalid_exceptions_fail(extra, category):
    with pytest.raises(GatesealError) as failure:
        selection(extra)
    assert failure.value.code == category


@pytest.mark.parametrize(
    "component,version", [("betterleaks", "1.8.0"), ("ruff", "0.17.1")]
)
def test_exception_for_unselected_component_fails(component, version):
    with pytest.raises(GatesealError) as failure:
        selection(exception(component, version), ("hygiene",))
    assert failure.value.code == "unused_exception"


def test_exceptions_must_satisfy_cross_component_requirements():
    """Refuse a reviewed version that needs a newer Betterleaks than the pin.

    Each version is reviewed on its own, but the selection must hold together.
    """
    tool = POLICY.tools["pre-commit-hooks"]
    demanding = dict(tool.versions)
    old = demanding["5.0.0"]
    demanding["5.0.0"] = dataclasses.replace(old, requires={"betterleaks": "9.0.0"})
    tools = dict(POLICY.tools)
    tools["pre-commit-hooks"] = dataclasses.replace(tool, versions=demanding)
    policy = dataclasses.replace(POLICY, tools=tools)
    with pytest.raises(GatesealError) as failure:
        resolve(manifest(exception("pre-commit-hooks", "5.0.0")), policy)
    assert failure.value.code == "incompatible_exception"
    assert "pre-commit-hooks 5.0.0 needs betterleaks 9.0.0" in str(failure.value)


def test_notes_name_active_overdue_and_redundant_exceptions():
    chosen = selection(
        exception("betterleaks", "1.8.0", review_by="2026-10-26")
        + exception("pre-commit-hooks", "6.0.0")
    )
    found = {note.category: note for note in notes(chosen, date(2026, 10, 27))}
    assert found["overdue_exception"].severity == "warning"
    assert "betterleaks 1.8.0 (policy pin 1.9.0)" in found["overdue_exception"].message
    assert "stays in effect" in found["overdue_exception"].message
    assert found["redundant_exception"].severity == "warning"
    assert found["policy_choice"].message == "Choice hygiene.exclude: nothing."
    active = notes(selection(exception("betterleaks", "1.8.0")), date(2026, 10, 27))
    assert [n.category for n in active] == ["policy_choice", "version_exception"]
    assert all(n.severity == "info" for n in active)


def test_notes_render_with_their_own_severity():
    chosen = selection(exception("betterleaks", "1.8.0", review_by="2026-01-01"))
    result = Result("status", "recorded", "ok", notes=notes(chosen, date(2026, 10, 27)))
    assert "Warning: Version exception betterleaks 1.8.0" in result.render(False)
    rendered = json.loads(result.render(True))
    assert [d["severity"] for d in rendered["diagnostics"]] == [
        "info",
        "info",
        "warning",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://user:hunter2@github.com/team/policy.git",
        "https://hunter2@github.com/team/policy.git",
        "https://github.com/team/policy.git?access=hunter2",
        "https://github.com/team/policy.git#main",
        "http://github.com/team/policy.git",
        "git://github.com/team/policy.git",
        "file:///srv/policy.git",
        "/srv/policy.git",
        "git@github.com:team/policy.git",
        "git@github.com:team/policy.git?token=hunter2",
        "ssh://git:hunter2@github.com/team/policy.git",
        "ssh://-oProxyCommand=x/team/policy.git",
        "https://github.com",
        "https://github.com/team/../policy.git",
    ],
)
def test_policy_repository_must_be_credential_free(url):
    with pytest.raises(GatesealError) as failure:
        git_source(url, ".", COMMIT, "v1")
    assert failure.value.code == "invalid_policy_source"
    assert "hunter2" not in str(failure.value) + failure.value.next_step


def test_scp_like_repository_suggests_its_url():
    with pytest.raises(GatesealError) as failure:
        git_source("git@github.com:team/policy.git", ".", COMMIT, "v1")
    assert "ssh://git@github.com/team/policy.git" in failure.value.next_step
    with pytest.raises(GatesealError) as failure:
        git_source("git@github.com:team/policy.git#hunter2", ".", COMMIT, "v1")
    assert "https://github.com/team/gateseal-policy.git" in failure.value.next_step


@pytest.mark.parametrize(
    "path,commit,tag",
    [
        ("../policy", COMMIT, "v1"),
        ("/policy", COMMIT, "v1"),
        (".", COMMIT.upper(), "v1"),
        (".", COMMIT[:12], "v1"),
        (".", COMMIT * 2, "v1"),
        (".", COMMIT, "refs/tags/../v1"),
        (".", COMMIT, "v1.lock"),
        (".", COMMIT, "-v1"),
        (".", COMMIT, "v1*"),
    ],
)
def test_policy_identity_is_exact(path, commit, tag):
    with pytest.raises(GatesealError):
        git_source("https://github.com/team/policy.git", path, commit, tag)


def test_git_manifest_requires_a_tag():
    text = render(
        ("hygiene",),
        git_source("https://github.com/team/policy.git", "policy", COMMIT, "v1"),
    )
    assert parse_manifest(text).source.tag == "v1"  # type: ignore[union-attr]
    with pytest.raises(GatesealError):
        parse_manifest(text.replace(b'tag = "v1"\n', b""))


def test_bundled_policy_needs_no_git():
    policy, checks = acquire(
        manifest(),
        None,
        preparation=None,
        git="/nonexistent/git",  # type: ignore[arg-type]
    )
    assert policy == POLICY and checks == ()
