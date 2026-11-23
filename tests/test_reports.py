"""A hook hands a report to a later hook: the reports a Tool Definition declares.

A team policy measures test coverage at a push and in the gate. Its measure
tool runs the tests and writes coverage.data, then converts it to
coverage.json. The hook of its score tool reads coverage.json. Each test loads
the policy from memory, as test_policy_review does, with a Policy Lock written
by hand, so no test needs an upstream.
"""

import json
import re

import pytest
from conftest import PIN
from gateseal import checks, ci, setup
from gateseal.diagnostics import NEXT_STEPS, GatesealError
from gateseal.lock import Locked, Snapshot, compile_outputs, describe
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import (
    LOCK_COMMAND,
    load_bundled_policy,
    load_policy,
    review_policy,
)
from gateseal.tools import built_in_tools, load_definition, reports
from gateseal.tools.model import Origin
from gateseal.validation import canonical
from installation import success
from stand_ins import INSTALLED, executable

CONFIG = ".pre-commit-config.yaml"
REPORTS = ".gateseal/reports"
IGNORE = f"{REPORTS}/.gitignore"
DATA = ".gateseal/reports/coverage.data"
JSON = ".gateseal/reports/coverage.json"
RUN = "[hooks.measure-run]"
TYPES = 'types_or = ["python", "pyi"]\n'
MEASURE = f"""\
kind = "pre-commit-repository"
stages = ["push", "ci"]
scope = "project"

[source]
repository = "https://example.org/team/measure"
tag = "v{{version}}"

{RUN}
name = "tests with coverage"
output = "plain"
args = ["run", "--data-file", "{{coverage.data}}"]
{TYPES}writes = ["coverage.data"]

[hooks.measure-json]
name = "coverage report"
output = "plain"
args = ["json", "--data-file", "{{coverage.data}}", "-o", "{{coverage.json}}"]
reads = ["coverage.data"]
writes = ["coverage.json"]
"""
SCORE = """\
kind = "pre-commit-repository"
stages = ["push", "ci"]
scope = "project"

[source]
repository = "https://example.org/team/score"
tag = "v{version}"

[hooks.score]
name = "CRAP score"
output = "plain"
args = ["--coverage", "{coverage.json}"]
reads = ["coverage.json"]
"""
TESTS = '[capabilities.tests.tools.measure]\npin = "1.0.0"\nreviewed = ["1.0.0"]\n'
SCORES = '[capabilities.scores.tools.score]\npin = "1.0.0"\nreviewed = ["1.0.0"]\n'
HEAD = 'schema = 5\nrelease = "team-1"\nminimum_compiler = "0.2.0"\n\n'
PUSH = 'stages = ["push", "ci"]'
COMMIT = 'stages = ["commit", "run", "ci"]'
GATE = 'stages = ["ci"]'
AT_PUSH = "at a push and in the gate"
OPTIONAL = (
    '\n[capabilities.tests]\noptional = ["measure-json"]\n'
    "\n[capabilities.tests.choices]\nskip = []\n\n"
)
SELECT_THE_WRITER = (
    "Select the capability whose tool writes the report in gateseal.toml, and keep "
    "the hook that writes it. Then run gateseal configure."
)


def reader(policy=HEAD + TESTS + "\n" + SCORES, measure=MEASURE, score=SCORE):
    """Return a reader of the team policy's files, each as a test edits it."""
    revision = {"versions": {"1.0.0": {"revision": "c0ffee" + "0" * 34}}}
    tools = {name: revision for name in ("measure", "score") if f".{name}]" in policy}
    files = {
        "policy.toml": policy.encode(),
        "tools/measure.toml": measure.encode(),
        "tools/score.toml": score.encode(),
        "policy.lock": canonical({"components": tools, "generated_by": LOCK_COMMAND}),
    }

    def read_asset(name):
        if name not in files:
            raise FileNotFoundError(name)
        return files[name]

    return read_asset


def measure(old="", new=""):
    """Return the measure definition with old replaced once, as a team edits it."""
    assert old in MEASURE
    return load_definition(
        "measure", MEASURE.replace(old, new, 1).encode(), Origin.TEAM
    )


def selection(policy, *capabilities, extra=""):
    manifest = render(capabilities, BundledSource(policy.release)) + extra.encode()
    return resolve(parse_manifest(manifest), policy)


def unwritten(call, *arguments, **options):
    """Return the refusal of a report that no earlier hook writes."""
    with pytest.raises(GatesealError) as failure:
        call(*arguments, **options)
    assert (failure.value.code, failure.value.exit_code) == ("missing_report", 2)
    return failure.value


def rendered(chosen):
    """Return each hook of the generated configuration, by its ID."""
    outputs = compile_outputs(chosen, PIN, BundledSource("team-1"))
    repositories = json.loads(outputs[CONFIG])["repos"]
    return {hook["id"]: hook for repo in repositories for hook in repo["hooks"]}


def test_a_hook_records_its_reports_and_passes_each_path_as_an_argument():
    hooks = measure().hooks
    assert (hooks["measure-run"].reads, hooks["measure-run"].writes) == (
        (),
        ("coverage.data",),
    )
    assert (hooks["measure-json"].reads, hooks["measure-json"].writes) == (
        ("coverage.data",),
        ("coverage.json",),
    )
    assert hooks["measure-json"].args == ("json", "--data-file", DATA, "-o", JSON)


def test_a_hook_without_reports_reads_and_writes_none():
    for hook in built_in_tools()["ruff"].hooks.values():
        assert (hook.reads, hook.writes) == ((), ())


@pytest.mark.parametrize(
    "old,new,field",
    [
        ('writes = ["coverage.data"]', 'writes = ["Coverage.data"]', "writes"),
        ('writes = ["coverage.data"]', 'writes = ["coverage"]', "writes"),
        ('writes = ["coverage.data"]', 'writes = ["coverage.data.old"]', "writes"),
        ('writes = ["coverage.data"]', 'writes = ["x/coverage.data"]', "writes"),
        ('writes = ["coverage.data"]', 'writes = [".gitignore"]', "writes"),
        ('writes = ["coverage.data"]', 'writes = ["coverage.data\\n"]', "writes"),
        ('writes = ["coverage.data"]', 'writes = "coverage.data"', "writes"),
        ('writes = ["coverage.data"]', "writes = [5]", "writes"),
        ('reads = ["coverage.data"]', 'reads = ["9.data"]', "reads"),
        ('reads = ["coverage.data"]', "reads = [5]", "reads"),
        (
            'writes = ["coverage.data"]',
            'writes = ["coverage.data", "coverage.data"]',
            "writes",
        ),
        (
            'reads = ["coverage.data"]',
            'reads = ["coverage.data", "coverage.data"]',
            "reads",
        ),
        ('reads = ["coverage.data"]', 'reads = ["coverage.json"]', "writes"),
        ('"{coverage.data}"]\n' + TYPES, '"{coverage.json}"]\n' + TYPES, "args"),
        ('"-o", "{coverage.json}"', '"--output={coverage.json}"', "args"),
        ('"-o", "{coverage.json}"', '"-o", "{coverage}"', "args"),
    ],
    ids=[
        "uppercase",
        "no extension",
        "two dots",
        "folder",
        "leading dot",
        "line break",
        "not a list",
        "written not text",
        "leading digit",
        "read not text",
        "written twice",
        "read twice",
        "read and written",
        "report of another hook",
        "inside an argument",
        "no such report",
    ],
)
def test_an_invalid_report_names_its_field(old, new, field):
    with pytest.raises(GatesealError) as failure:
        measure(old, new)
    assert failure.value.code == "invalid_field"
    named = rf"tools\.measure\.hooks\.measure-(run|json)\.{field}\b"
    assert re.match(named, str(failure.value))


def test_every_kind_takes_reports():
    uv_project = MEASURE.replace("pre-commit-repository", "uv-project").replace(
        'repository = "https://example.org/team/measure"\ntag = "v{version}"',
        'package = "coverage"\n\n[executable]\nname = "coverage"',
    )
    definition = load_definition("measure", uv_project.encode(), Origin.TEAM)
    assert definition.hooks["measure-run"].args == ("run", "--data-file", DATA)


def test_a_selection_renders_each_report_path_into_its_hooks():
    hooks = rendered(selection(load_policy(reader()), "scores", "tests"))
    assert [(name, hook.get("args")) for name, hook in hooks.items()][-3:] == [
        ("measure-run", ["run", "--data-file", DATA]),
        ("measure-json", ["json", "--data-file", DATA, "-o", JSON]),
        ("score", ["--coverage", JSON]),
    ]
    assert hooks["measure-run"]["always_run"] is False
    assert hooks["measure-run"]["types_or"] == ["python", "pyi"]
    assert (hooks["measure-json"]["always_run"], "types_or" in hooks["score"]) == (
        True,
        False,
    )
    assert all(hooks[name]["pass_filenames"] is False for name in list(hooks)[-3:])


def test_a_policy_runs_each_writing_hook_before_the_hook_that_reads_its_report():
    chosen = selection(load_policy(reader()), "scores", "tests")
    assert [hook.id for hook in chosen.hooks] == [
        "measure-run",
        "measure-json",
        "score",
    ]


@pytest.mark.parametrize(
    "files,hook,path,when",
    [
        ({"policy": HEAD + SCORES + "\n" + TESTS}, "score", JSON, AT_PUSH),
        ({"policy": HEAD + SCORES}, "score", JSON, AT_PUSH),
        (
            {"policy": HEAD + TESTS + 'hook_ids = ["measure-json", "measure-run"]\n'},
            "measure-json",
            DATA,
            AT_PUSH,
        ),
        ({"measure": MEASURE.replace(PUSH, GATE)}, "score", JSON, AT_PUSH),
        ({"measure": MEASURE.replace(PUSH, COMMIT)}, "score", JSON, AT_PUSH),
        (
            {"score": SCORE.replace(PUSH, COMMIT)},
            "score",
            JSON,
            "at a commit, in gateseal run and in the gate",
        ),
        (
            {"score": SCORE.replace("coverage.json", "coverage.xml")},
            "score",
            ".gateseal/reports/coverage.xml",
            AT_PUSH,
        ),
    ],
    ids=[
        "reading tool first",
        "no writing tool",
        "reading hook first",
        "written in the gate alone",
        "written at a commit",
        "read at a commit",
        "only other reports written",
    ],
)
def test_a_policy_whose_hook_reads_an_unwritten_report_is_refused(
    files, hook, path, when
):
    """An earlier hook writes it, in a tool with every stage of the reader's tool."""
    refusal = unwritten(review_policy, reader(**files))
    assert str(refusal) == (
        f"Hook {hook} reads {path}, but no hook before it writes that report {when}."
    )
    assert refusal.next_step == NEXT_STEPS["missing_report"]


@pytest.mark.parametrize(
    "files",
    [
        {"score": SCORE.replace(PUSH, GATE)},
        {"measure": MEASURE.replace(PUSH, COMMIT), "score": SCORE.replace(PUSH, GATE)},
    ],
    ids=["written at a push", "written at a commit"],
)
def test_a_hook_of_the_gate_alone_reads_what_any_stage_set_writes(files):
    assert review_policy(reader(**files)).release == "team-1"


def test_a_manifest_that_leaves_the_writing_capability_out_is_refused():
    refusal = unwritten(selection, load_policy(reader()), "scores")
    assert str(refusal) == (
        f"Hook score reads {JSON}, but no hook before it writes that report {AT_PUSH}."
    )
    assert refusal.next_step == SELECT_THE_WRITER


def test_a_manifest_that_skips_the_writing_hook_is_refused():
    policy = load_policy(reader(HEAD + TESTS + OPTIONAL + SCORES))
    skip = '\n[choices.tests]\nskip = ["measure-json"]\n'
    refusal = unwritten(selection, policy, "scores", "tests", extra=skip)
    assert "Hook score reads" in str(refusal)
    assert refusal.next_step == SELECT_THE_WRITER
    assert len(selection(policy, "scores", "tests").hooks) == 3


def test_a_selection_that_writes_a_report_owns_an_ignore_file_for_every_report(
    tmp_path,
):
    """Git then ignores each report, and tracks the ignore file itself."""
    chosen = selection(load_policy(reader()), "scores", "tests")
    outputs = compile_outputs(chosen, PIN, BundledSource("team-1"))
    assert outputs[IGNORE] == b"*\n!.gitignore\n"
    success(["git", "init", "--quiet", tmp_path], tmp_path)
    for name, content in {IGNORE: outputs[IGNORE], DATA: b"", JSON: b"{}"}.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes(content)
    status = ["git", "status", "--porcelain", "--untracked-files=all"]
    assert success(status, tmp_path).stdout == f"?? {IGNORE}\n"


def test_a_selection_that_writes_no_report_owns_no_ignore_file():
    bundled = load_bundled_policy()
    writing = {"python-tests", "python-crap"}
    chosen = selection(bundled, *(c for c in bundled.capabilities if c not in writing))
    outputs = compile_outputs(chosen, PIN, BundledSource(bundled.release))
    assert not [name for name in outputs if name.startswith(".gateseal/reports")]


NAMES = {
    "measure-run": "tests with coverage",
    "measure-json": "coverage report",
    "score": "CRAP score",
}
EVERY_FILE = ci.full_scope()


def configured(root, policy, *capabilities):
    """Return root as configure leaves it for capabilities, with its owned files."""
    source = BundledSource(policy.release)
    manifest = render(capabilities, source)
    chosen = resolve(parse_manifest(manifest), policy)
    outputs = compile_outputs(chosen, PIN, source)
    for name, content in outputs.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(content)
    return Locked(root, policy, PIN, Snapshot(manifest, b"", outputs), source, chosen)


def gate(locked, monkeypatch, written=(), status=0, **statuses):
    """Run the gate where a script stands in for pre-commit.

    The script records the reports it finds, writes the reports of written,
    prints each hook's status line, Passed unless statuses names another, and
    exits with status.
    """
    lines = [f"ls -A {REPORTS} > ../found"]
    lines += [f"echo report > {name}" for name in written]
    for hook, name in NAMES.items():
        ended = statuses.get(hook.replace("-", "_"), "Passed")
        lines.append(f"echo '{name}{'.' * 9}{ended}'")
    script = "#!/bin/sh\n" + INSTALLED + "\n".join([*lines, f"exit {status}\n"])
    python = executable(locked.root.parent / "stand-in" / "python", script)
    monkeypatch.setattr(setup, "require_pre_commit", lambda: str(python))
    return checks.gate(None, locked, EVERY_FILE)


@pytest.fixture
def measured(tmp_path):
    """A repository that selects both capabilities, with reports of an earlier run.

    Its report folder also holds a file and a folder that no hook declares.
    """
    root = tmp_path / "repository"
    locked = configured(root, load_policy(reader()), "scores", "tests")
    for name in (DATA, JSON, f"{REPORTS}/notes.txt"):
        (root / name).write_text("earlier\n")
    (root / REPORTS / "shards").mkdir()
    return locked


def outcome(result):
    return result.code, result.exit_code


def test_the_gate_removes_the_reports_of_an_earlier_run_before_pre_commit(
    measured, monkeypatch
):
    result = gate(measured, monkeypatch)
    found = (measured.root.parent / "found").read_text().split()
    assert found == [".gitignore", "notes.txt", "shards"]
    assert outcome(result) == ("missing_report", 2)


def test_the_gate_passes_when_each_hook_finds_the_report_it_reads(
    measured, monkeypatch
):
    result = gate(measured, monkeypatch, written=(DATA, JSON))
    assert outcome(result) == ("checks_passed", 0)
    assert (measured.root / JSON).read_text() == "report\n"


@pytest.mark.parametrize(
    "written,status,statuses,hook,path,writer",
    [
        ((DATA,), 0, {}, "score", JSON, "measure-json"),
        ((DATA,), 1, {"score": "Failed"}, "score", JSON, "measure-json"),
        ((JSON,), 0, {}, "measure-json", DATA, "measure-run"),
        ((), 1, {"measure_json": "Failed"}, "measure-json", DATA, "measure-run"),
    ],
    ids=["reader passed", "reader failed", "first reader passed", "both lack theirs"],
)
def test_a_hook_that_ran_without_a_report_it_reads_fails_the_gate(
    measured, monkeypatch, written, status, statuses, hook, path, writer
):
    """It also outranks a failed check, and keeps what pre-commit reported."""
    result = gate(measured, monkeypatch, written, status, **statuses)
    assert outcome(result) == ("missing_report", 2)
    assert result.message == (
        f"Hook {hook} ran, but {path}, which {writer} writes before it, is missing. "
        f"{EVERY_FILE.reason}"
    )
    assert len(result.next_steps) == 1 and "writes the report" in result.next_steps[0]
    hooks = [entry for entry in result.entries if entry["category"] == "hook"]
    assert [entry["hook"] for entry in hooks] == list(NAMES)
    assert "CRAP score" in result.details


@pytest.mark.parametrize(
    "written,status,statuses,expected",
    [
        (
            (),
            0,
            {"measure_json": "(no files to check)Skipped", "score": "Skipped"},
            ("checks_passed", 0),
        ),
        ((DATA, JSON), 1, {"score": "Failed"}, ("checks_failed", 1)),
        (
            (),
            1,
            {"measure_run": "Failed", "measure_json": "Failed", "score": "Failed"},
            ("checks_failed", 1),
        ),
        ((), 3, {}, ("tool_failed", 4)),
    ],
    ids=[
        "readers skipped",
        "check failed with its report",
        "writer failed",
        "pre-commit stopped",
    ],
)
def test_the_gate_ends_as_before_unless_a_hook_ran_without_its_report(
    measured, monkeypatch, written, status, statuses, expected
):
    result = gate(measured, monkeypatch, written, status, **statuses)
    assert outcome(result) == expected


def results(**statuses):
    """Return each hook's result as pre-commit reported it, then a finding of it."""
    found = []
    for hook, status in statuses.items():
        found.append({"category": "hook", "hook": hook, "status": status})
        found.append({"category": "finding", "hook": hook, "message": "found"})
    return found


@pytest.mark.parametrize(
    "files,statuses,expected",
    [
        (
            (DATA,),
            {"measure-json": "passed", "score": "failed"},
            f"Hook score ran, but {JSON}, which measure-json writes before it, "
            "is missing.",
        ),
        ((DATA,), {"measure-json": "failed", "score": "passed"}, None),
    ],
    ids=["reader failed", "writer failed"],
)
def test_a_finding_keeps_the_status_of_its_hook(measured, files, statuses, expected):
    """A finding names its hook but holds no status."""
    reports.clear(measured.root, measured.selection.hooks)
    for name in files:
        (measured.root / name).write_text("report\n")
    reads = measured.selection.reads
    assert reports.missing(measured.root, reads, results(**statuses)) == expected


def test_the_gate_leaves_the_folder_of_a_selection_that_writes_no_report(
    tmp_path, monkeypatch
):
    locked = configured(tmp_path / "repository", load_bundled_policy(), "hygiene")
    kept = locked.root / JSON
    kept.parent.mkdir(parents=True)
    kept.write_text("kept\n")
    assert outcome(gate(locked, monkeypatch)) == ("checks_passed", 0)
    assert kept.read_text() == "kept\n"


AGAIN = (
    '\n[hooks.again]\nname = "again"\noutput = "plain"\nwrites = ["coverage.json"]\n'
)


def report_notes(root, **files):
    """Return what check and status say about the reports of both capabilities."""
    locked = configured(root, load_policy(reader(**files)), "scores", "tests")
    found = [note for note in describe(locked) if note.category == "report"]
    assert all(note.severity == "info" for note in found)
    return [note.message for note in found]


def test_check_and_status_name_each_report_a_hook_reads_and_its_writer(tmp_path):
    assert report_notes(tmp_path) == [
        f"Hook measure-json reads {DATA}, which measure-run writes before it "
        f"{AT_PUSH}.",
        f"Hook score reads {JSON}, which measure-json writes before it {AT_PUSH}.",
    ]


@pytest.mark.parametrize(
    "writer,reader,when",
    [
        (COMMIT, COMMIT, "at a commit, in gateseal run and in the gate"),
        (GATE, GATE, "in the gate"),
        (PUSH, GATE, AT_PUSH),
    ],
    ids=["at a commit", "in the gate alone", "read in the gate alone"],
)
def test_a_report_note_says_when_the_writing_hook_runs(tmp_path, writer, reader, when):
    files = {
        "measure": MEASURE.replace(PUSH, writer),
        "score": SCORE.replace(PUSH, reader),
    }
    assert report_notes(tmp_path, **files)[1] == (
        f"Hook score reads {JSON}, which measure-json writes before it {when}."
    )


def test_a_hook_reads_the_report_of_the_last_hook_that_writes_it(tmp_path):
    assert report_notes(tmp_path, measure=MEASURE + AGAIN)[1] == (
        f"Hook score reads {JSON}, which again writes before it {AT_PUSH}."
    )
