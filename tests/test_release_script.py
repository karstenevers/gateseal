import ast
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

RELEASE = Path(__file__).resolve().parents[1] / "ci" / "release.py"
spec = importlib.util.spec_from_file_location("release", RELEASE)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
sys.modules["release"] = release
spec.loader.exec_module(release)

INIT = '__version__ = "0.2.0"\n'
LINKS = (
    "[Unreleased]: https://github.com/o/r/compare/v0.2.0...HEAD\n"
    "[0.2.0]: https://github.com/o/r/compare/v0.1.0...v0.2.0\n"
    "[0.1.0]: https://github.com/o/r/releases/tag/v0.1.0\n"
)


def changelog(heading="## [0.2.0] - 2026-11-11", body="### Fixed\n\n- A fix.\n"):
    return (
        "# Changelog\n\nIntro.\n\n## [Unreleased]\n\n"
        f"{heading}\n\n{body}\n"
        "## [0.1.0] - 2026-10-01\n\nThe first release.\n\n" + LINKS
    )


def pyproject(version):
    return f'[project]\nname = "gateseal"\nversion = "{version}"\n'


def test_script_parses_as_python_3_12():
    ast.parse(RELEASE.read_text(), feature_version=(3, 12))


@pytest.mark.parametrize(
    ("head", "base", "expected"),
    [
        ("0.2.0", "0.2.0", release.Comparison.SAME),
        ("0.2.0", "0.1.0", release.Comparison.CHANGED),
        ("0.2.0", None, release.Comparison.NO_BASE),
    ],
)
def test_compare_versions(head, base, expected):
    assert release.compare(head, base) is expected


def test_project_version_reads_pyproject():
    assert release.project_version(pyproject("1.2.3")) == "1.2.3"


def test_release_with_dated_section_passes():
    release.refuse_release("0.2.0", INIT, changelog(), tag_exists=False)


@pytest.mark.parametrize(
    ("init", "text", "tag_exists", "fix"),
    [
        ('__version__ = "0.1.0"\n', changelog(), False, "__version__"),
        (INIT, changelog(heading="## [0.3.0] - 2026-11-11"), False, "no section"),
        (INIT, changelog(heading="## [0.2.0] - Unreleased"), False, "Date it"),
        (INIT, changelog(heading="## [0.2.0]"), False, "needs a date"),
        (INIT, changelog(heading="## [0.2.0] - 2026-02-30"), False, "needs a date"),
        (INIT, changelog(heading="## [0.2.0] - 20261111"), False, "needs a date"),
        (INIT, changelog(body=""), False, "is empty"),
        (INIT, changelog(), True, "tag v0.2.0 exists"),
    ],
)
def test_release_refusals(init, text, tag_exists, fix):
    with pytest.raises(release.Refusal, match=fix):
        release.refuse_release("0.2.0", init, text, tag_exists)


def test_notes_are_the_section_body():
    assert release.changelog_section(changelog(), "0.2.0") == "### Fixed\n\n- A fix."


def test_notes_of_the_last_section_leave_out_link_references():
    assert release.changelog_section(changelog(), "0.1.0") == "The first release."


def test_last_section_with_only_link_references_is_empty():
    text = "# Changelog\n\n## [0.1.0] - 2026-10-01\n\n" + LINKS
    with pytest.raises(release.Refusal, match="is empty"):
        release.changelog_section(text, "0.1.0")


def run(status, conclusion=None, url="https://example.test/run"):
    return {"status": status, "conclusion": conclusion, "html_url": url}


@pytest.mark.parametrize(
    ("runs", "expected"),
    [
        ([], "pending"),
        ([run("queued")], "pending"),
        ([run("in_progress")], "pending"),
        ([run("completed", "success")], "passed"),
        ([run("completed", "failure")], "failed"),
        ([run("completed", "cancelled")], "failed"),
        ([run("completed", "success"), run("completed", "failure")], "passed"),
        ([run("completed", "failure"), run("completed", "success")], "failed"),
        ([run("in_progress"), run("completed", "success")], "pending"),
    ],
)
def test_run_state_follows_the_newest_run(runs, expected):
    assert release.run_state(runs) == release.RunState(expected)


SHA = "a" * 64
WHEEL = "gateseal-0.2.0-py3-none-any.whl"
URL = f"https://files.example.test/{WHEEL}"


def entry(filename=WHEEL, sha256=SHA, packagetype="bdist_wheel"):
    return {
        "filename": filename,
        "packagetype": packagetype,
        "digests": {"sha256": sha256},
        "url": URL,
    }


def test_served_wheel_waits_while_no_wheel_is_listed():
    sdist = entry("gateseal-0.2.0.tar.gz", packagetype="sdist")
    assert release.served_wheel({"urls": [sdist]}, "0.2.0", SHA) is None
    assert release.served_wheel({"urls": []}, "0.2.0", SHA) is None


def test_served_wheel_refuses_another_file_name():
    other = entry("gateseal-0.2.0-py3-none-manylinux_2_17_x86_64.whl")
    with pytest.raises(release.Refusal, match=WHEEL):
        release.served_wheel({"urls": [other]}, "0.2.0", SHA)


def test_served_wheel_refuses_another_digest():
    with pytest.raises(release.Refusal, match="b" * 64):
        release.served_wheel({"urls": [entry(sha256="b" * 64)]}, "0.2.0", SHA)


def test_served_wheel_returns_the_url_of_the_built_wheel():
    sdist = entry("gateseal-0.2.0.tar.gz", packagetype="sdist")
    assert release.served_wheel({"urls": [sdist, entry()]}, "0.2.0", SHA) == URL


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    """A clone whose origin is a local bare repository at version 0.1.0."""
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(clone))
    git(clone, "config", "user.name", "Release Test")
    git(clone, "config", "user.email", "release@example.test")
    (clone / "src" / "gateseal").mkdir(parents=True)
    commit(clone, "0.1.0", changelog(heading="## [0.1.0] - 2026-10-01"))
    git(clone, "push", "-q", "origin", "main")
    return clone


def commit(clone, version, text):
    (clone / "pyproject.toml").write_text(pyproject(version))
    (clone / "src" / "gateseal" / "__init__.py").write_text(
        f'__version__ = "{version}"\n'
    )
    (clone / "CHANGELOG.md").write_text(text)
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", f"Version {version}")
    return git(clone, "rev-parse", "HEAD")


def check(clone, event="push", before="", ref="refs/heads/main"):
    output = clone.parent / "output"
    summary = clone.parent / "summary"
    environment = os.environ | {
        "EVENT_NAME": event,
        "BEFORE_SHA": before,
        "GITHUB_REF": ref,
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    result = subprocess.run(
        [sys.executable, str(RELEASE), "check"],
        cwd=clone,
        env=environment,
        capture_output=True,
        text=True,
    )
    outputs = output.read_text().splitlines() if output.exists() else []
    return result, outputs, summary.read_text() if summary.exists() else ""


def test_check_releases_nothing_when_the_version_stays(repository):
    before = git(repository, "rev-parse", "HEAD")
    (repository / "notes.txt").write_text("notes\n")
    git(repository, "add", "notes.txt")
    git(repository, "commit", "-q", "-m", "Add notes")
    result, outputs, summary = check(repository, before=before)
    assert result.returncode == 0, result.stdout
    assert outputs == ["version=0.1.0", "release=false"]
    assert "stayed at 0.1.0" in summary


@pytest.mark.parametrize("before", ["", "0" * 40, "f" * 40])
def test_check_releases_nothing_without_a_comparison_base(repository, before):
    result, outputs, _ = check(repository, before=before)
    assert result.returncode == 0, result.stdout
    assert outputs == ["version=0.1.0", "release=false"]
    assert "::notice::" in result.stdout


def test_check_releases_a_changed_version(repository):
    before = git(repository, "rev-parse", "HEAD")
    commit(repository, "0.2.0", changelog())
    result, outputs, summary = check(repository, before=before)
    assert result.returncode == 0, result.stdout
    assert outputs == ["version=0.2.0", "release=true"]
    assert "0.2.0: releasing" in summary


def test_check_refuses_a_version_whose_tag_exists(repository):
    before = git(repository, "rev-parse", "HEAD")
    head = commit(repository, "0.2.0", changelog())
    git(repository, "push", "-q", "origin", f"{head}:refs/tags/v0.2.0")
    result, outputs, _ = check(repository, before=before)
    assert result.returncode == 1
    assert "::error::The tag v0.2.0 exists" in result.stdout
    assert outputs == []


def test_manual_start_releases_the_current_version(repository):
    result, outputs, _ = check(repository, event="workflow_dispatch")
    assert result.returncode == 0, result.stdout
    assert outputs == ["version=0.1.0", "release=true"]


def test_manual_start_outside_main_is_refused(repository):
    result, outputs, _ = check(
        repository, event="workflow_dispatch", ref="refs/heads/topic"
    )
    assert result.returncode == 1
    assert "::error::" in result.stdout
    assert outputs == []
