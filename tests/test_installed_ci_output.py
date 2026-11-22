"""In CI, the installed gate's log shows color, the hooks' changes and annotations.

A consumer of the bundled policy's hygiene capability adds a repository hook
that prints a colored token and fails on a text file that says LEAK. Each test
stages files and runs the installed gateseal gate with the variables that
GitHub Actions or Azure Pipelines sets, on real pre-commit and hooks.
"""

import pytest
from conftest import on_machine
from gateseal.diagnostics import COLOR
from installation import clone, process, success

pytestmark = pytest.mark.integration

HALF = "A1b2C3d4E5f6G7h8I9j0"
SCRIPT = f"""#!/bin/sh
for file in "$@"; do
  if grep -q LEAK "$file"; then
    printf '\\033[31mtoken ghp_{HALF}\\033[1m{HALF}\\033[0m in %s\\n' "$file"
    exit 1
  fi
done
"""
TABLE = (
    "\n[hooks.no-leaks]\n"
    'name = "Refuse LEAK"\n'
    'run = "scripts/no-leaks.sh"\n'
    'files = "\\\\.txt$"\n'
)
HEADER = "Changes that the hooks made:"
FIXED = "trim trailing whitespace: Failed, files were modified by this hook"
PROVIDERS = {
    "github": ({"GITHUB_ACTIONS": "true"}, "::error title=gateseal::"),
    "azure": ({"TF_BUILD": "True"}, "##vso[task.logissue type=error]"),
}


@pytest.fixture(scope="module")
def module_hooked(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("ci output consumer ü").resolve()
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        script = consumer / "scripts/no-leaks.sh"
        script.parent.mkdir()
        script.write_text(SCRIPT)
        script.chmod(0o755)
        installed.cli(consumer, "configure", "--capability", "hygiene")
        manifest = consumer / "gateseal.toml"
        manifest.write_text(manifest.read_text() + TABLE)
        installed.cli(consumer, "configure")
        success(["git", "add", "-A"], consumer)
        success(["git", "commit", "-q", "-m", "Add a repository hook"], consumer)
        success(["git", "tag", "adopted"], consumer)
    return consumer


@pytest.fixture
def hooked(module_hooked):
    """The consumer, reset to its adoption after each test."""
    yield module_hooked
    success(["git", "reset", "-q", "--hard", "adopted"], module_hooked)
    success(["git", "clean", "-fdq"], module_hooked)


@pytest.fixture(params=sorted(PROVIDERS))
def provider(request):
    return request.param


def gate(installed, root, provider, agent, files):
    """Stage files, then return the gate's text output in provider's CI."""
    for name, data in files.items():
        (root / name).write_bytes(data)
    success(["git", "add", *files], root)
    env = dict(
        installed.environment,
        **PROVIDERS[provider][0],
        GITHUB_STEP_SUMMARY=str(agent / "step summary.md"),
        AGENT_TEMPDIRECTORY=str(agent),
    )
    return process([installed.command, "--repo", root, "gate"], root, env)


def lines(shown):
    return COLOR.sub("", shown.stdout).splitlines()


def errors(shown, provider):
    command = PROVIDERS[provider][1]
    return [line for line in shown.stdout.splitlines() if line.startswith(command)]


def summary(provider, agent):
    name = "step summary.md" if provider == "github" else "gateseal-summary.md"
    return (agent / name).read_text()


def test_the_log_shows_color(installed, hooked, provider, tmp_path):
    shown = gate(installed, hooked, provider, tmp_path, {"notes.txt": b"ready\n"})
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert "\x1b[" in shown.stdout
    assert any(line.endswith("Passed") for line in lines(shown))
    assert errors(shown, provider) == []


def test_a_hook_that_changes_a_file_shows_its_change(
    installed, hooked, provider, tmp_path
):
    files = {"notes.txt": b"trailing space  \n"}
    shown = gate(installed, hooked, provider, tmp_path, files)
    assert shown.returncode == 1, shown.stdout + shown.stderr
    plain = lines(shown)
    changes = plain[plain.index(HEADER) :]
    assert {"-trailing space  ", "+trailing space"} <= set(changes)
    assert errors(shown, provider) == [PROVIDERS[provider][1] + FIXED]
    assert f"- {FIXED}" in summary(provider, tmp_path).splitlines()
    if provider == "azure":
        upload = f"##vso[task.uploadsummary]{tmp_path / 'gateseal-summary.md'}"
        assert shown.stdout.splitlines()[-1] == upload


def test_two_hooks_that_change_two_files_show_both_changes(
    installed, hooked, provider, tmp_path
):
    files = {"spaces.txt": b"trailing space  \n", "ending.txt": b"no final newline"}
    shown = gate(installed, hooked, provider, tmp_path, files)
    assert shown.returncode == 1, shown.stdout + shown.stderr
    changes = lines(shown)[lines(shown).index(HEADER) :]
    assert {
        "diff --git a/spaces.txt b/spaces.txt",
        "-trailing space  ",
        "+trailing space",
        "diff --git a/ending.txt b/ending.txt",
        "-no final newline",
        "+no final newline",
    } <= set(changes)


def test_a_failed_gate_that_changed_no_file_shows_no_diff(
    installed, hooked, provider, tmp_path
):
    files = {"broken.yaml": b"key: [unclosed\n"}
    shown = gate(installed, hooked, provider, tmp_path, files)
    assert shown.returncode == 1, shown.stdout + shown.stderr
    assert HEADER not in shown.stdout
    assert errors(shown, provider) == [PROVIDERS[provider][1] + "check yaml: Failed"]


def test_no_part_of_a_secret_reaches_the_log_an_annotation_or_the_summary(
    installed, hooked, provider, tmp_path
):
    files = {
        "notes.txt": f"token ghp_{HALF}{HALF}  \n".encode(),
        "leak.txt": b"LEAK\n",
    }
    shown = gate(installed, hooked, provider, tmp_path, files)
    assert shown.returncode == 1, shown.stdout + shown.stderr
    assert HALF not in shown.stdout + shown.stderr
    plain = lines(shown)
    changes = plain[plain.index(HEADER) :]
    assert {"-token [redacted]  ", "+token [redacted]"} <= set(changes)
    assert "token [redacted] in leak.txt" in plain
    assert len(errors(shown, provider)) == 2
    assert HALF not in summary(provider, tmp_path)
