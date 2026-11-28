"""The dependency checks of a uv project: lockfile, deptry and audit.

A consumer with a src-layout uv project selects the bundled policy's lockfile
check and deptry, and each commit runs both. The audit asks a vulnerability
service, so these tests run it against a local service: a team release whose
audit tool is the bundled definition with only the service URL replaced. The
first commit installs the pinned uv from PyPI, and deptry with uv.
"""

import json
import os
import socket
from importlib.resources import files

import pytest
from conftest import on_machine
from gateseal.validation import canonical
from gateseal.worktree import locate_worktree
from installation import clone, process, success
from osv_service import IDNA_ADVISORY, osv_service
from stand_ins import configure as configure_in_process
from team_policy import URL, environment, git, publish

pytestmark = pytest.mark.integration

OSV = "https://api.osv.dev"
ADVISORY = IDNA_ADVISORY["id"]
AUDIT_DEFINITION = (
    files("gateseal").joinpath("data", "tools", "uv-audit.tool.toml").read_text()
)
NEW_HOOKS = {"uv-lock", "uv-audit", "deptry"}
STATUSES = ("Passed", "Failed", "Skipped")


PYPROJECT = """\
[project]
name = "labels"
version = "0"
requires-python = ">=3.12"
dependencies = ["idna==3.10"]

[tool.deptry]
known_first_party = ["labels"]
"""


def project(idna="3.10", extra=""):
    """Return the files of a src-layout uv project that imports idna."""
    return {
        "pyproject.toml": PYPROJECT.replace("3.10", idna) + extra,
        "src/labels/__init__.py": (
            "import idna\n\n\ndef encode(name: str) -> bytes:\n"
            "    return idna.encode(name)\n"
        ),
        "main.py": 'from labels import encode\n\nprint(encode("example.com"))\n',
    }


def write(root, files):
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)


def adopt(installed, root, capabilities, files, env=None, source=()):
    """Write and lock files in root, configure capabilities and commit."""
    write(root, files)
    success(["uv", "lock"], root)
    flags = [option for name in capabilities for option in ("--capability", name)]
    installed.cli(root, "configure", *flags, *source, env=env)
    success(["git", "add", "-A"], root)
    success(["git", "commit", "-m", "Adopt dependency checks"], root, env)
    return root


def commit(root, env=None):
    result = process(["git", "commit", "-m", "Change"], root, env)
    return result, result.stdout + result.stderr


def hook_output(output, name):
    """Return what pre-commit printed below the status line of hook name."""
    lines = output.splitlines()
    (start,) = [i for i, line in enumerate(lines) if line.startswith(name + "...")]
    rest = lines[start + 1 :]
    end = next(
        (i for i, line in enumerate(rest) if line.endswith(STATUSES)),
        len(rest),
    )
    return "\n".join([lines[start], *rest[:end]])


def hook_statuses(result):
    """Return each hook's status in a run or gate result, by hook id."""
    entries = [d for d in result["diagnostics"] if d["category"] == "hook"]
    return {entry["hook"]: entry["status"] for entry in entries}


def details(result):
    """Return the output of pre-commit that a result carries."""
    return result["diagnostics"][1]["message"]


@pytest.fixture(scope="module")
def module_checked(installed, tmp_path_factory):
    """A consumer that runs the lockfile check and deptry at each commit."""
    root = tmp_path_factory.mktemp("dependencies consumer ü").resolve()
    with on_machine(installed):
        consumer = clone(root / "origin", root / "consumer")
        capabilities = ("hygiene", "python-lock", "python-dependencies")
        adopt(installed, consumer, capabilities, project())
        success(["git", "tag", "adopted"], consumer)
    return consumer


@pytest.fixture
def checked(module_checked):
    yield module_checked
    success(["git", "reset", "-q", "--hard", "adopted"], module_checked)
    success(["git", "clean", "-fdq"], module_checked)


def stage(root, files):
    write(root, files)
    success(["git", "add", "--", *files], root)


def test_the_clean_project_passes_both_checks(installed, checked):
    config = json.loads((checked / ".pre-commit-config.yaml").read_bytes())
    entries = {hook["id"]: hook for repo in config["repos"] for hook in repo["hooks"]}
    assert entries["uv-lock"]["args"] == ["--check"]
    assert entries["deptry"]["entry"].endswith(
        "-- python .gateseal/deptry-launcher.txt --no-ansi ."
    )
    stage(checked, {"src/labels/names.py": "import idna\n\nNAME = idna.decode\n"})
    result, output = commit(checked)
    assert result.returncode == 0, output
    ran = hook_statuses(installed.cli(checked, "run"))
    assert (ran["uv-lock"], ran["deptry"]) == ("passed", "passed")


def test_a_stale_uv_lock_stops_the_commit_and_names_uv_lock(checked):
    project_file = (checked / "pyproject.toml").read_text()
    newer = project_file.replace('"idna==3.10"', '"idna==3.11"')
    stage(checked, {"pyproject.toml": newer})
    result, output = commit(checked)
    assert result.returncode == 1, output
    shown = hook_output(output, "uv lock check")
    assert shown.splitlines()[0].endswith("Failed")
    assert "run `uv lock`" in shown
    heads = success(["git", "rev-parse", "HEAD", "adopted"], checked).stdout.split()
    assert heads[0] == heads[1]
    success(["uv", "lock"], checked)
    success(["git", "add", "uv.lock"], checked)
    result, output = commit(checked)
    assert result.returncode == 0, output


def test_an_unused_dependency_stops_the_commit(checked):
    project_file = (checked / "pyproject.toml").read_text()
    unused = project_file.replace('"idna==3.10"', '"idna==3.10", "attrs==26.1.0"')
    write(checked, {"pyproject.toml": unused})
    success(["uv", "lock"], checked)
    success(["git", "add", "pyproject.toml", "uv.lock"], checked)
    result, output = commit(checked)
    assert result.returncode == 1, output
    assert (
        "pyproject.toml: DEP002 'attrs' defined as a dependency but not used in the "
        "codebase"
    ) in hook_output(output, "deptry")


def test_an_undeclared_import_stops_the_commit(checked):
    stage(checked, {"src/labels/settings.py": "import yaml\n"})
    result, output = commit(checked)
    assert result.returncode == 1, output
    assert (
        "src/labels/settings.py:1:8: DEP001 'yaml' imported but missing from the "
        "dependency definitions"
    ) in hook_output(output, "deptry")


@pytest.mark.parametrize("key", ["exclude", "extend_exclude"])
def test_deptry_skips_the_tool_environment_and_keeps_the_own_exclusions(checked, key):
    """The commit passes, so deptry read neither environment nor docs.

    An exclude replaces deptry's default, which skips the files that Git
    ignores. python-dateutil in .gateseal/environments/deptry imports six,
    which the project does not declare, and docs/conf.py imports yaml.
    """
    project_file = (checked / "pyproject.toml").read_text()
    assert project_file.endswith('known_first_party = ["labels"]\n')
    dependencies = '"idna==3.10", "python-dateutil==2.9.0.post0"'
    stage(
        checked,
        {
            "pyproject.toml": project_file.replace('"idna==3.10"', dependencies)
            + f'{key} = ["docs"]\n',
            "src/labels/dates.py": "import dateutil\n",
            "docs/conf.py": "import yaml\n",
        },
    )
    success(["uv", "lock"], checked)
    success(["git", "add", "uv.lock"], checked)
    result, output = commit(checked)
    assert result.returncode == 0, output


def test_deptry_reads_the_project_packages_with_a_space_in_the_uv_cache(
    checked, tmp_path
):
    """deptry maps pyyaml to its module yaml through the installed package.

    With a space in the uv cache path, uv starts a console script outside the
    project, where deptry would guess the module name and report pyyaml as
    unused and yaml as missing.
    """
    project_file = (checked / "pyproject.toml").read_text()
    stage(
        checked,
        {
            "pyproject.toml": project_file.replace(
                '"idna==3.10"', '"idna==3.10", "pyyaml==6.0.3"'
            ),
            "src/labels/settings.py": "import yaml\n\nSETTINGS = yaml.safe_load('')\n",
        },
    )
    env = dict(os.environ, UV_CACHE_DIR=str(tmp_path / "uv cache"))
    success(["uv", "lock"], checked, env)
    success(["git", "add", "uv.lock"], checked)
    result, output = commit(checked, env)
    assert result.returncode == 0, output


def test_selecting_python_alone_adds_no_dependency_check(tmp_path):
    root = tmp_path / "repository"
    success(["git", "init", "--quiet", "--initial-branch=main", root], tmp_path)
    configure_in_process(locate_worktree(root), ("hygiene", "python"))
    config = json.loads((root / ".pre-commit-config.yaml").read_bytes())
    ids = {hook["id"] for repo in config["repos"] for hook in repo["hooks"]}
    assert "ruff-check" in ids and not ids & NEW_HOOKS


def local_audit(url, ignore=()):
    """Return the change that runs the bundled audit against the service at url.

    Built-in names are reserved, so the team's definition is local-audit: the
    bundled text with only the service URL replaced. ignore fills the
    policy's list of ignored advisories.
    """

    def change(data):
        assert AUDIT_DEFINITION.count(json.dumps(OSV)) == 1
        (data / "tools").mkdir(exist_ok=True)
        (data / "tools/local-audit.toml").write_text(
            AUDIT_DEFINITION.replace(json.dumps(OSV), json.dumps(url))
        )
        policy = data / "policy.toml"
        policy.write_text(
            policy.read_text().replace(
                "[capabilities.python-audit.tools.uv-audit]",
                "[capabilities.python-audit.tools.local-audit]",
            )
        )
        lock = json.loads((data / "policy.lock").read_bytes())
        lock["components"]["local-audit"] = lock["components"].pop("uv-audit")
        (data / "policy.lock").write_bytes(canonical(lock))
        rules = data / "rules/uv-audit.toml"
        rules.write_text(
            rules.read_text().replace("ignore = []", f"ignore = {json.dumps(ignore)}")
        )

    return change


def closed_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(scope="module")
def service():
    with osv_service() as running:
        yield running


@pytest.fixture(scope="module")
def origin(service, tmp_path_factory):
    """The team policy: v1 asks the service, v2 a closed port, v3 ignores."""
    origin = tmp_path_factory.mktemp("audit policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", local_audit(service.url))
    publish(origin, "v2", local_audit(f"http://127.0.0.1:{closed_port()}"))
    publish(origin, "v3", local_audit(service.url, [ADVISORY]))
    return origin


def audited(installed, origin, root, tag, files):
    """Return a consumer of the team release tag with files, and its environment."""
    env = environment(installed, origin)
    consumer = clone(root / "origin", root / "consumer")
    source = ("--policy", URL, "--policy-path", "policy", "--policy-tag", tag)
    adopt(installed, consumer, ("hygiene", "python-audit"), files, env, source)
    return consumer, env


def gate(installed, consumer, env, code):
    return installed.cli(consumer, "gate", code=code, env=env)


def test_the_audit_runs_only_in_the_gate(installed, origin, service, tmp_path):
    """A vulnerable lock passes a commit, a push and gateseal run, then fails the gate.

    A repository hook at a push gives the clone a push launcher, so pre-commit
    runs at the push.
    """
    consumer, env = audited(installed, origin, tmp_path, "v1", project("3.6"))
    script = consumer / "scripts/ready.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho ready\n")
    script.chmod(0o755)
    manifest = consumer / "gateseal.toml"
    manifest.write_text(
        manifest.read_text() + '\n[hooks.ready]\nname = "ready"\n'
        'run = "scripts/ready.sh"\nstages = ["push", "ci"]\n'
    )
    installed.cli(consumer, "configure", env=env)
    installed.cli(consumer, "setup", "--refresh", env=env)
    service.requests.clear()
    success(["git", "add", "-A"], consumer)
    result, output = commit(consumer, env)
    assert result.returncode == 0, output
    assert "uv audit" not in output
    pushed = process(["git", "push", "origin", "HEAD:refs/heads/topic"], consumer, env)
    shown = pushed.stdout + pushed.stderr
    assert pushed.returncode == 0, shown
    assert "ready" in shown and "uv audit" not in shown
    ran = installed.cli(consumer, "run", env=env)
    assert "uv-audit" not in hook_statuses(ran) and "uv audit" not in details(ran)
    assert service.requests == []
    gated = gate(installed, consumer, env, 1)
    assert gated["code"] == "checks_failed"
    assert hook_statuses(gated)["uv-audit"] == "failed"
    assert "POST /v1/querybatch" in service.requests


def test_the_gate_names_the_package_the_advisory_and_the_fix(
    installed, origin, tmp_path
):
    consumer, env = audited(installed, origin, tmp_path, "v1", project("3.6"))
    gated = gate(installed, consumer, env, 1)
    assert gated["code"] == "checks_failed"
    shown = details(gated)
    assert "idna 3.6 has 1 known vulnerability" in shown
    assert ADVISORY in shown and "Fixed in: 3.7" in shown


def test_a_clean_project_passes_the_gate(installed, origin, tmp_path):
    consumer, env = audited(installed, origin, tmp_path, "v1", project())
    gated = gate(installed, consumer, env, 0)
    assert hook_statuses(gated)["uv-audit"] == "passed"


def test_an_unreachable_service_fails_the_gate_as_a_tool_failure(
    installed, origin, tmp_path
):
    consumer, env = audited(installed, origin, tmp_path, "v2", project())
    gated = gate(installed, consumer, env, 4)
    assert gated["code"] == "tool_failed"
    assert (
        "The uv-audit hook stopped with exit code 2."
        in gated["diagnostics"][0]["message"]
    )
    assert "network access" in gated["next_steps"][0]
    assert hook_statuses(gated)["uv-audit"] == "stopped"


def test_an_advisory_that_the_policy_ignores_passes(installed, origin, tmp_path):
    consumer, env = audited(installed, origin, tmp_path, "v3", project("3.6"))
    rules = (consumer / ".gateseal/local-audit.toml").read_text()
    assert f'ignore = ["{ADVISORY}"]' in rules
    gated = gate(installed, consumer, env, 0)
    assert hook_statuses(gated)["uv-audit"] == "passed"


@pytest.mark.parametrize(
    "files",
    [
        project("3.6", f'\n[tool.uv.audit]\nignore = ["{ADVISORY}"]\n'),
        project("3.6") | {"uv.toml": f'[audit]\nignore = ["{ADVISORY}"]\n'},
    ],
    ids=["pyproject.toml", "uv.toml"],
)
def test_a_repository_file_cannot_ignore_an_advisory(
    installed, origin, tmp_path, files
):
    consumer, env = audited(installed, origin, tmp_path, "v1", files)
    gated = gate(installed, consumer, env, 1)
    assert hook_statuses(gated)["uv-audit"] == "failed"
    assert ADVISORY in details(gated)


@pytest.mark.network
def test_the_bundled_audit_asks_the_real_service(installed, tmp_path):
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    adopt(installed, consumer, ("hygiene", "python-audit"), project("3.6"))
    gated = gate(installed, consumer, None, 1)
    assert gated["code"] == "checks_failed"
    assert "idna 3.6 has" in details(gated)
