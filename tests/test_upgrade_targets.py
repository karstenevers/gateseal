"""Plan gateseal upgrade's targets and report how each one moved.

Each test compiles a consumer's selection or Lock from a copy of the bundled
policy in process. It asks the planner which Version Exceptions an upgrade's
targets add, change, keep or remove before anything is written, or which
moves the upgrade reports between two Locks.
"""

import copy
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from conftest import PIN, named_flags
from gateseal import __version__, upgrade
from gateseal.diagnostics import GatesealError
from gateseal.lock import Locked, Snapshot, compile_lock
from gateseal.managed_files import LOCK
from gateseal.manifest import (
    REASON_LIMIT,
    BundledSource,
    ExceptionRequest,
    git_source,
    parse_manifest,
    render,
    resolve,
)
from gateseal.policy import bundled_root, load_bundled_policy, load_policy_tree
from gateseal.validation import canonical, decode_json
from test_upgrade_worktree import REASON, REVIEW_BY, older_pins

KEPT_REASON = "Kept for the migration"
KEPT_DATE = date(2026, 12, 31)
EXCEPTION_FLAGS = {"--reason", "--review-by"}


def reviews_hooks_4_6(root):
    toml = root / "policy.toml"
    toml.write_text(
        toml.read_text().replace(
            'reviewed = ["6.0.0", "5.0.0"]',
            'reviewed = ["6.0.0", "5.0.0", "4.6.0"]',
        )
    )
    lock = json.loads((root / "policy.lock").read_bytes())
    lock["components"]["pre-commit-hooks"]["versions"]["4.6.0"] = {"revision": "4" * 40}
    (root / "policy.lock").write_bytes(canonical(lock))


@pytest.fixture(scope="module")
def policies(tmp_path_factory):
    """The bundled policy, one pinning older versions and one reviewing 4.6.0."""
    found = {"bundled": load_bundled_policy()}
    for name, edit in (("older", older_pins), ("three", reviews_hooks_4_6)):
        root = tmp_path_factory.mktemp(name) / "policy"
        shutil.copytree(bundled_root(), root, ignore=shutil.ignore_patterns("*.md"))
        edit(root)
        found[name] = load_policy_tree(root)
    assert sorted(found["three"].tools["pre-commit-hooks"].versions) == [
        "4.6.0",
        "5.0.0",
        "6.0.0",
    ]
    return found


def exception(component, version, reason=KEPT_REASON, review_by=KEPT_DATE):
    return (
        f'\n[exceptions.{component}]\nversion = "{version}"\nreason = "{reason}"\n'
        f"review_by = {review_by.isoformat()}\n"
    )


def current(policy, *exceptions, capabilities=("hygiene", "secrets")):
    """Return the Lock state a consumer configured with exceptions has."""
    source = BundledSource(policy.release)
    manifest = render(capabilities, source) + "".join(exceptions).encode()
    selection = resolve(parse_manifest(manifest), policy)
    return Locked(Path(), policy, PIN, Snapshot(manifest, b"", {}), source, selection)


def changes(locked, policy, components, reason=None, review_by=None):
    request = upgrade.Request(None, components, reason, review_by)
    manifest = parse_manifest(locked.snapshot.manifest)
    return upgrade._exception_changes(manifest, locked, policy, request)


def refused(*arguments, **flags):
    with pytest.raises(GatesealError) as raised:
        changes(*arguments, **flags)
    return raised.value


def test_targets_name_each_component_once_with_an_optional_version():
    assert upgrade.components(["betterleaks", "pre-commit-hooks=5.0.0"]) == {
        "betterleaks": None,
        "pre-commit-hooks": "5.0.0",
    }
    assert upgrade.components([]) == {}


@pytest.mark.parametrize(
    ("arguments", "code", "named"),
    [
        (
            ["betterleaks", "betterleaks=1.8.0"],
            "invalid_invocation",
            "'betterleaks=1.8.0'",
        ),
        (["Better Leaks"], "invalid_invocation", "'Better Leaks'"),
        (["=1.8.0"], "invalid_invocation", "'=1.8.0'"),
        (["betterleaks=latest"], "invalid_field", "betterleaks's target"),
        (["betterleaks="], "invalid_field", "betterleaks's target"),
    ],
)
def test_invalid_targets_are_refused(arguments, code, named):
    with pytest.raises(GatesealError) as raised:
        upgrade.components(arguments)
    assert raised.value.code == code
    assert named in str(raised.value)


@pytest.mark.parametrize("argument", ["betterleaks=latest", "betterleaks="])
def test_an_invalid_target_names_the_argument_in_its_step(argument):
    with pytest.raises(GatesealError) as raised:
        upgrade.components([argument])
    assert "betterleaks=VERSION" in raised.value.next_step
    assert "file" not in raised.value.next_step


@pytest.mark.parametrize(
    ("name", "code", "why", "step"),
    [
        ("pre-commit", "runtime_target", "Compiler Runtime", ("gateseal upgrade",)),
        ("ruff", "unsupported_component", "capabilities", ()),
        ("shellcheck", "unsupported_component", "offers", ()),
    ],
)
def test_a_target_outside_the_selected_components_names_why(
    policies, name, code, why, step
):
    bundled = policies["bundled"]
    error = refused(current(bundled), bundled, {name: None})
    assert error.code == code
    assert all(value in str(error) for value in (name, why))
    assert all(value in error.next_step for value in step)
    assert error.next_step


def test_another_reviewed_version_gets_an_exception_with_both_flags(policies):
    bundled = policies["bundled"]
    found = changes(
        current(bundled), bundled, {"betterleaks": "1.8.0"}, REASON, REVIEW_BY
    )
    assert found == (
        {"betterleaks": ExceptionRequest("betterleaks", "1.8.0", REASON, REVIEW_BY)},
        frozenset(),
    )


@pytest.mark.parametrize(
    ("reason", "review_by"), [(None, None), (REASON, None), (None, REVIEW_BY)]
)
def test_another_reviewed_version_without_both_flags_is_refused(
    policies, reason, review_by
):
    bundled = policies["bundled"]
    error = refused(
        current(bundled), bundled, {"betterleaks": "1.8.0"}, reason, review_by
    )
    assert error.code == "invalid_exception"
    assert all(value in str(error) for value in ("betterleaks 1.8.0", "1.9.0"))
    assert named_flags(error.next_step) == EXCEPTION_FLAGS


@pytest.mark.parametrize("reason", [" ", "r" * (REASON_LIMIT + 1), "two\nlines"])
def test_a_reason_flag_the_manifest_would_refuse_names_the_flag(policies, reason):
    bundled = policies["bundled"]
    error = refused(
        current(bundled), bundled, {"betterleaks": "1.8.0"}, reason, REVIEW_BY
    )
    assert error.code == "invalid_exception"
    assert named_flags(str(error)) == named_flags(error.next_step) == {"--reason"}


def test_a_reason_flag_holds_at_most_the_limit(policies):
    bundled = policies["bundled"]
    longest = "r" * REASON_LIMIT
    exceptions, _ = changes(
        current(bundled), bundled, {"betterleaks": "1.8.0"}, longest, REVIEW_BY
    )
    assert exceptions["betterleaks"].reason == longest


def test_an_unreviewed_target_lists_the_reviewed_versions(policies):
    bundled = policies["bundled"]
    error = refused(current(bundled), bundled, {"betterleaks": "1.7.0"})
    assert error.code == "unknown_version"
    assert str(error).endswith("1.8.0, 1.9.0.")
    assert "betterleaks 1.7.0" in str(error)


def test_a_kept_exception_keeps_its_reason_and_date_unless_a_flag_replaces_them(
    policies,
):
    bundled = policies["bundled"]
    kept = current(bundled, exception("betterleaks", "1.8.0"))
    target = {"betterleaks": "1.8.0"}
    assert changes(kept, bundled, target) == ({}, frozenset())
    renewed = changes(kept, bundled, target, review_by=REVIEW_BY)
    assert renewed[0] == {
        "betterleaks": ExceptionRequest("betterleaks", "1.8.0", KEPT_REASON, REVIEW_BY)
    }
    explained = changes(kept, bundled, target, reason=REASON)
    assert explained[0] == {
        "betterleaks": ExceptionRequest("betterleaks", "1.8.0", REASON, KEPT_DATE)
    }


def test_a_bare_target_below_the_pin_removes_its_exception(policies):
    bundled = policies["bundled"]
    kept = current(bundled, exception("betterleaks", "1.8.0"))
    assert changes(kept, bundled, {"betterleaks": None}) == (
        {"betterleaks": None},
        frozenset(),
    )


def test_moving_a_kept_exception_to_a_third_version_needs_both_flags(policies):
    three = policies["three"]
    kept = current(three, exception("pre-commit-hooks", "5.0.0"))
    target = {"pre-commit-hooks": "4.6.0"}
    for flags in ({}, {"reason": REASON}):
        error = refused(kept, three, target, **flags)
        assert error.code == "invalid_exception"
        assert all(value in str(error) for value in ("pre-commit-hooks 4.6.0", "6.0.0"))
        assert named_flags(error.next_step) == EXCEPTION_FLAGS
    moved = changes(kept, three, target, REASON, REVIEW_BY)
    assert moved[0] == {
        "pre-commit-hooks": ExceptionRequest(
            "pre-commit-hooks", "4.6.0", REASON, REVIEW_BY
        )
    }


def test_a_bare_target_keeps_a_newer_reviewed_version_as_an_exception(policies):
    newer = current(policies["bundled"])
    older = policies["older"]
    error = refused(newer, older, {"betterleaks": None})
    assert error.code == "invalid_exception"
    assert all(value in str(error) for value in ("betterleaks 1.9.0", "1.8.0"))
    assert named_flags(error.next_step) == EXCEPTION_FLAGS
    assert changes(newer, older, {"betterleaks": None}, REASON, REVIEW_BY) == (
        {"betterleaks": ExceptionRequest("betterleaks", "1.9.0", REASON, REVIEW_BY)},
        frozenset({"betterleaks"}),
    )


def test_a_bare_target_keeps_the_exception_that_selects_a_newer_version(policies):
    older = policies["older"]
    kept = current(older, exception("betterleaks", "1.9.0"))
    assert changes(kept, older, {"betterleaks": None}) == (
        {},
        frozenset({"betterleaks"}),
    )
    assert changes(kept, older, {"betterleaks": None}, review_by=REVIEW_BY) == (
        {
            "betterleaks": ExceptionRequest(
                "betterleaks", "1.9.0", KEPT_REASON, REVIEW_BY
            )
        },
        frozenset({"betterleaks"}),
    )


@pytest.mark.parametrize(
    ("reason", "review_by"), [(REASON, None), (None, REVIEW_BY), (REASON, REVIEW_BY)]
)
def test_flags_without_a_version_to_select_are_refused(policies, reason, review_by):
    bundled = policies["bundled"]
    error = refused(
        current(bundled), bundled, {"pre-commit-hooks": None}, reason, review_by
    )
    assert error.code == "invalid_invocation"
    assert named_flags(str(error)) == EXCEPTION_FLAGS


def test_flags_fill_one_exception_while_another_target_removes_its_own(policies):
    bundled = policies["bundled"]
    kept = current(bundled, exception("pre-commit-hooks", "5.0.0"))
    targets = {"betterleaks": "1.8.0", "pre-commit-hooks": None}
    assert changes(kept, bundled, targets, REASON, REVIEW_BY) == (
        {
            "betterleaks": ExceptionRequest("betterleaks", "1.8.0", REASON, REVIEW_BY),
            "pre-commit-hooks": None,
        },
        frozenset(),
    )


def lock_document(policy, *exceptions, source=None):
    """Return the Lock configure writes for hygiene and secrets, as JSON reads it."""
    source = source or BundledSource(policy.release)
    manifest = render(("hygiene", "secrets"), source) + "".join(exceptions).encode()
    selection = resolve(parse_manifest(manifest), policy)
    document = compile_lock(manifest, source, policy, PIN, selection)
    return decode_json(canonical(document))


def released(document, version, commit):
    """Return document as the gateseal release version at commit pins it."""
    moved = copy.deepcopy(document)
    moved["gateseal"].update(version=version, commit=commit)
    return moved


def moves(before, after, preserved=frozenset()):
    return {
        entry["target"]: (entry["from"], entry["to"], entry["state"], entry["message"])
        for entry in upgrade.report(before, after, preserved)
        if entry["category"] == "upgrade"
    }


def exception_moves(before, after):
    return {
        entry["component"]: (entry["version"], entry["status"], entry["severity"])
        for entry in upgrade.report(before, after, frozenset())
        if entry["category"] == "exception"
    }


def test_a_gateseal_move_reports_gateseal_alone(policies):
    old = released(lock_document(policies["bundled"]), "0.1.0", "a" * 40)
    new = released(lock_document(policies["older"]), "0.2.0", "b" * 40)
    before, after = f"0.1.0 at {'a' * 40}", f"0.2.0 at {'b' * 40}"
    message = f"gateseal {before} to {after}: upgrade."
    assert moves(old, new) == {"gateseal": (before, after, "upgrade", message)}
    assert moves(new, old)["gateseal"][2] == "rollback"
    assert exception_moves(old, new) == {}


def test_another_commit_of_one_release_is_a_change(policies):
    document = lock_document(policies["bundled"])
    other = released(document, __version__, "b" * 40)
    pinned = f"{__version__} at {PIN.commit}"
    assert moves(document, other)["gateseal"][2] == "change"
    assert moves(document, document)["gateseal"][2:] == (
        "unchanged",
        f"gateseal {pinned}: unchanged.",
    )


def test_a_policy_move_names_both_releases(policies):
    bundled, older = policies["bundled"], policies["older"]
    commit = "c" * 40
    source = git_source(
        "https://policy.example/team/policy.git", "policy", commit, "v1"
    )
    moved = moves(lock_document(bundled), lock_document(older, source=source))
    old = f"bundled {bundled.release} ({bundled.content_digest})"
    new = f"v1 at {commit} ({older.content_digest})"
    assert moved["policy"] == (old, new, "change", f"policy {old} to {new}: change.")
    assert moved["pre-commit-hooks"][:3] == ("6.0.0", "5.0.0", "rollback")
    assert moved["betterleaks"][:3] == ("1.9.0", "1.8.0", "rollback")


def test_a_preserved_component_names_the_pin_it_is_newer_than(policies):
    before = lock_document(policies["bundled"])
    after = lock_document(policies["older"], exception("betterleaks", "1.9.0"))
    moved = moves(before, after, frozenset({"betterleaks"}))["betterleaks"]
    assert moved[:3] == ("1.9.0", "1.9.0", "preserved")
    assert "1.8.0" in moved[3]
    assert moves(before, after)["betterleaks"][2] == "unchanged"


@pytest.mark.parametrize(
    ("old", "kept", "new", "made", "expected"),
    [
        ("bundled", [], "bundled", [("betterleaks", "1.8.0")], ("1.8.0", "added")),
        ("bundled", [("betterleaks", "1.8.0")], "bundled", [], ("1.8.0", "removed")),
        (
            "bundled",
            [("pre-commit-hooks", "5.0.0")],
            "older",
            [("pre-commit-hooks", "5.0.0")],
            ("5.0.0", "redundant"),
        ),
        (
            "bundled",
            [("betterleaks", "1.8.0")],
            "bundled",
            [("betterleaks", "1.8.0")],
            ("1.8.0", "retained"),
        ),
        (
            "three",
            [("pre-commit-hooks", "5.0.0")],
            "three",
            [("pre-commit-hooks", "4.6.0")],
            ("4.6.0", "changed"),
        ),
    ],
)
def test_each_exception_reports_how_it_moved(policies, old, kept, new, made, expected):
    before = lock_document(policies[old], *(exception(*item) for item in kept))
    after = lock_document(policies[new], *(exception(*item, REASON) for item in made))
    (name,) = {item[0] for item in (*kept, *made)}
    severity = "warning" if expected[1] == "redundant" else "info"
    assert exception_moves(before, after) == {name: (*expected, severity)}


def test_an_exception_entry_names_the_version_and_the_pin(policies):
    bundled = policies["bundled"]
    after = lock_document(bundled, exception("betterleaks", "1.8.0"))
    (entry,) = [
        e
        for e in upgrade.report(lock_document(bundled), after, frozenset())
        if e["category"] == "exception"
    ]
    named = ("betterleaks 1.8.0", "pin 1.9.0", "added")
    assert all(value in entry["message"] for value in named)


def test_a_gateseal_move_names_setup_of_the_new_release(policies):
    after = lock_document(policies["bundled"])
    before = released(after, "0.0.9", "a" * 40)
    result = upgrade._result(
        before, upgrade.Plan({LOCK: canonical(after)}, ()), (LOCK,)
    )
    assert (result.code, result.changed) == ("upgraded", (LOCK,))
    setup, review = result.next_steps
    assert setup == (
        f"Have each developer run uvx gateseal@{__version__} setup, so git commit "
        "runs pre-commit 4.7.0."
    )
    assert "git diff" in review
    moved = f"gateseal 0.0.9 at {'a' * 40} to {__version__} at {PIN.commit}: upgrade."
    assert result.details.split("\n") == [moved]
