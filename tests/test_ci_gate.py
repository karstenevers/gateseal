import json

import pytest
from conftest import preserved
from installation import clone, package_version, process, success

pytestmark = pytest.mark.integration

SECRET = "AKIA" + "Q7XK2M9P4R8T6W3Z"
WORKFLOW = ".github/workflows/gateseal.yml"
TRACKED = (
    "gateseal.toml",
    "gateseal.lock",
    ".pre-commit-config.yaml",
    ".gateseal/betterleaks-history-report.tmpl",
    ".gateseal/betterleaks-report.tmpl",
    ".gateseal/betterleaks.toml",
    ".gateseal/mise.toml",
    WORKFLOW,
)
ZEROS = "0" * 40


def source():
    wheel = f"gateseal-{package_version()}-py3-none-any.whl"
    return (
        f"https://raw.githubusercontent.com/example/wheels/{'e' * 40}/artifacts/{wheel}"
    )


def configure(installed, root, code=0, *compiler):
    return installed.cli(
        root,
        "configure",
        "--capability",
        "hygiene",
        "--capability",
        "secrets",
        "--ci",
        "github",
        *(compiler or ("--compiler-source", source())),
        "--compiler-sha256",
        installed.checksum,
        code=code,
    )


def head(root):
    return success(["git", "rev-parse", "HEAD"], root).stdout.strip()


def unchecked(root, name, text, message):
    """Commit text as name without the hook, as a push from elsewhere would."""
    (root / name).write_text(text)
    success(["git", "add", name], root)
    success(["git", "commit", "--no-verify", "-q", "-m", message], root)
    return head(root)


@pytest.fixture(scope="module")
def gated(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("gated consumer ü").resolve()
    consumer = clone(root / "origin", root / "consumer space ü ")
    configure(installed, consumer)
    activated = installed.cli(consumer, "activate", "--compiler-wheel", installed.wheel)
    assert activated["code"] == "prepared"
    success(["git", "add", *TRACKED], consumer)
    committed = process(["git", "commit", "-m", "Adopt the CI gate"], consumer)
    assert committed.returncode == 0, committed.stdout + committed.stderr
    return consumer


@pytest.fixture
def adopted(gated):
    start = head(gated)
    yield gated
    success(["git", "reset", "-q", "--hard", start], gated)


def test_configure_owns_the_workflow_and_check_guards_it(installed, consumer):
    assert WORKFLOW in configure(installed, consumer)["changed"]
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert WORKFLOW in lock["outputs"]
    assert lock["compiler"]["source"] == {"kind": "https-wheel", "location": source()}
    assert installed.cli(consumer, "check")["code"] == "consistent"
    unprepared = installed.cli(consumer, "gate", code=3)
    assert unprepared["code"] == "needs_activation"
    with preserved(consumer / WORKFLOW) as original:
        (consumer / WORKFLOW).write_bytes(original.replace(b"read", b"write"))
        edited = installed.cli(consumer, "check", code=2)
        assert edited["code"] == "ownership_conflict"
        assert WORKFLOW in json.dumps(edited)
    manifest = consumer / "gateseal.toml"
    with preserved(manifest) as selected:
        manifest.write_bytes(
            selected.replace(b'[ci]\nprovider = "github"\n', b"").replace(
                b'default_branch = "main"\n', b""
            )
        )
        with preserved(consumer / WORKFLOW) as original:
            (consumer / WORKFLOW).write_bytes(original + b"# kept\n")
            kept = installed.cli(consumer, "configure", code=2)
            assert kept["code"] == "ownership_conflict"
            assert (consumer / WORKFLOW).exists()
        dropped = installed.cli(consumer, "configure")
        assert WORKFLOW in dropped["changed"]
        assert not (consumer / WORKFLOW).exists()
        assert (
            WORKFLOW
            not in json.loads((consumer / "gateseal.lock").read_bytes())["outputs"]
        )


def test_a_local_wheel_or_a_linked_github_directory_is_refused(
    installed, consumer, tmp_path
):
    local = configure(installed, consumer, 2, "--compiler-wheel", installed.wheel)
    assert (local["code"], local["next_steps"]) == (
        "ci_needs_network_compiler",
        [
            "Run gateseal configure with --compiler-source and --compiler-sha256 "
            "instead of --compiler-wheel."
        ],
    )
    assert not (consumer / "gateseal.lock").exists()
    (tmp_path / "elsewhere").mkdir()
    (consumer / ".github").symlink_to(tmp_path / "elsewhere")
    assert configure(installed, consumer, 2)["code"] == "unsafe_path"
    assert not list((tmp_path / "elsewhere").iterdir())


def test_a_lock_that_pins_a_local_wheel_cannot_add_ci(installed, configured):
    manifest = configured / "gateseal.toml"
    manifest.write_bytes(
        manifest.read_bytes()
        + b'\n[ci]\nprovider = "github"\ndefault_branch = "main"\n'
    )
    refused = installed.cli(configured, "configure", code=2)
    assert (refused["code"], refused["next_steps"]) == (
        "ci_needs_network_compiler",
        [
            "Move gateseal.lock aside and run gateseal configure with "
            "--compiler-source and --compiler-sha256."
        ],
    )


def test_every_file_passes_in_the_ci_stage(installed, adopted):
    result = installed.cli(adopted, "gate")
    assert result["code"] == "checks_passed"
    entries = [d for d in result["diagnostics"] if d["category"] == "hook"]
    assert {entry["hook"] for entry in entries} >= {"betterleaks", "check-yaml"}
    assert {entry["stage"] for entry in entries} == {"ci"}
    assert not [d for d in result["diagnostics"] if d["category"] == "history"]


def test_a_range_checks_only_the_files_it_changes(installed, adopted):
    kept = unchecked(adopted, "keep.txt", f"key = {SECRET}\n", "Keep a key")
    clean = unchecked(adopted, "other.txt", "clean\n", "Clean")
    passed = installed.cli(adopted, "gate", "--base", kept, "--head", clean)
    assert passed["code"] == "checks_passed"
    history = [d for d in passed["diagnostics"] if d["category"] == "history"]
    assert [(d["tool"], d["status"]) for d in history] == [("betterleaks", "passed")]
    changed = unchecked(
        adopted, "keep.txt", f"key = {SECRET}\nmore\n", "Change the kept file"
    )
    failed = installed.cli(adopted, "gate", "--base", clean, "--head", changed, code=1)
    assert failed["code"] == "checks_failed"
    findings = [d for d in failed["diagnostics"] if d["category"] == "finding"]
    assert [(d["file"], d["hook"], d["stage"]) for d in findings] == [
        ("keep.txt", "betterleaks", "ci")
    ]
    assert "Fix each finding at its file and line." in failed["next_steps"]
    assert SECRET not in json.dumps(failed)


def test_a_removed_secret_fails_the_history_scan(installed, adopted):
    base = head(adopted)
    # -diff makes git log -p print no patch for the file unless asked for text.
    unchecked(adopted, ".gitattributes", "leak.txt -diff\n", "Hide leak diffs")
    leaked = unchecked(adopted, "leak.txt", f"key = {SECRET}\n", "Leak")
    success(["git", "rm", "-q", "leak.txt"], adopted)
    success(["git", "commit", "-q", "-m", "Remove the leak"], adopted)
    scope = ["gate", "--base", base, "--head", head(adopted)]
    result = installed.cli(adopted, *scope, code=1)
    assert result["code"] == "checks_failed"
    findings = [d for d in result["diagnostics"] if d["category"] == "finding"]
    assert [(f["file"], f["hook"], f["commit"]) for f in findings] == [
        ("leak.txt", "history", leaked)
    ]
    assert findings[0]["rule"] == "aws-access-token"
    assert result["next_steps"] == ["Fix each finding at its file and line."]
    human = process(
        [installed.command, "--repo", adopted, "--verbose", *scope],
        adopted,
        installed.environment,
    )
    assert human.returncode == 1, human.stdout + human.stderr
    assert "leak.txt:1:7: aws-access-token" in human.stdout, human.stdout
    for output in (json.dumps(result), human.stdout + human.stderr):
        assert SECRET not in output


def test_a_secret_that_a_merge_added_fails_the_history_scan(installed, adopted):
    base = head(adopted)
    success(["git", "checkout", "-q", "-b", "side"], adopted)
    unchecked(adopted, "side.txt", "side\n", "Side")
    success(["git", "checkout", "-q", "-"], adopted)
    unchecked(adopted, "main.txt", "main\n", "Main")
    success(["git", "merge", "-q", "--no-ff", "--no-commit", "side"], adopted)
    merge = unchecked(adopted, "evil.txt", f"key = {SECRET}\n", "Merge side")
    success(["git", "branch", "-D", "side"], adopted)
    success(["git", "rm", "-q", "evil.txt"], adopted)
    success(["git", "commit", "-q", "-m", "Remove the merged key"], adopted)
    result = installed.cli(
        adopted, "gate", "--base", base, "--head", head(adopted), code=1
    )
    findings = [d for d in result["diagnostics"] if d["category"] == "finding"]
    assert [(f["file"], f["hook"], f["commit"]) for f in findings] == [
        ("evil.txt", "history", merge)
    ]
    assert SECRET not in json.dumps(result)


def test_events_without_a_checkable_range(installed, adopted):
    current = head(adopted)
    deleted = installed.cli(adopted, "gate", "--before", current, "--after", ZEROS)
    assert deleted["code"] == "nothing_to_check"
    unknown = installed.cli(
        adopted, "gate", "--base", "1" * 40, "--head", current, code=2
    )
    assert unknown["code"] == "unknown_revision"
    assert "fetch-depth: 0" in unknown["next_steps"][0]
    half = installed.cli(adopted, "gate", "--base", current, code=2)
    assert half["code"] == "invalid_invocation"


def test_a_named_head_must_be_checked_out_unchanged(installed, adopted):
    base = head(adopted)
    after = unchecked(adopted, "notes.txt", "one\n", "Notes")
    scope = ["gate", "--base", base, "--head", after]
    for changes, staged in (("edited\n", None), ("staged\n", "staged\n")):
        (adopted / "notes.txt").write_text(changes)
        if staged:
            success(["git", "add", "notes.txt"], adopted)
        refused = installed.cli(adopted, *scope, code=2)
        assert refused["code"] == "uncommitted_changes"
        assert refused["next_steps"] == [
            "Commit or stash the changes, then run gateseal gate again."
        ]
        assert (adopted / "notes.txt").read_text() == changes
    (adopted / "notes.txt").write_text("one\n")
    restored = installed.cli(adopted, *scope, code=2)
    assert restored["code"] == "uncommitted_changes", (
        "a staged edit hid by the worktree"
    )
    success(["git", "reset", "-q", "--hard", after], adopted)
    success(["git", "checkout", "-q", "--detach", base], adopted)
    moved = installed.cli(adopted, *scope, code=2)
    assert moved["code"] == "head_not_checked_out"
    assert moved["next_steps"] == [f"Check out {after}, then run gateseal gate again."]
    success(["git", "checkout", "-q", "-"], adopted)
