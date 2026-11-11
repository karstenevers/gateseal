from importlib.resources import files

import pytest
import yaml
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError
from gateseal.github import CHECKOUT, MISE_SHA256, MISE_URL, UV_SHA256, UV_URL
from gateseal.lock import compile_lock, compile_outputs
from gateseal.manifest import CI as Table
from gateseal.manifest import BundledSource, git_source, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from gateseal.tools import load_definition
from gateseal.tools.model import HOOK_STAGES, Origin, Stage

WORKFLOW = ".github/workflows/gateseal.yml"
WHEEL = "gateseal-0.1.0-py3-none-any.whl"
NETWORK = CompilerArtifact(
    "https-wheel",
    f"https://raw.githubusercontent.com/example/wheels/{'a' * 40}/artifacts/" + WHEEL,
    WHEEL,
    "b" * 64,
    "0.1.0",
)
LOCAL = CompilerArtifact("local-wheel", f"/wheels/{WHEEL}", WHEEL, "b" * 64, "0.1.0")
CI = '\n[ci]\nprovider = "github"\ndefault_branch = "release/2.x"\n'
GIT = "https://github.com/team/gateseal-policy.git"
POLICY = load_bundled_policy()


def outputs(manifest, artifact=NETWORK, source=None):
    parsed = parse_manifest(manifest)
    return compile_outputs(
        resolve(parsed, POLICY), artifact, source or BundledSource(POLICY.release)
    )


def bundled(extra=CI):
    return render(("hygiene", "secrets"), BundledSource(POLICY.release)) + (
        extra.encode()
    )


def workflow(source=None):
    document = yaml.safe_load(outputs(bundled(), source=source)[WORKFLOW])
    return document, document["jobs"]["gateseal"]


def test_the_workflow_guards_every_event_with_read_only_full_history():
    document, job = workflow()
    events = document[True]
    assert set(events) == {
        "push",
        "pull_request",
        "merge_group",
        "workflow_dispatch",
        "schedule",
    }
    assert events["push"] == {"branches": ["release/2.x"]}
    assert all(
        value is None
        for name, value in events.items()
        if name not in ("push", "schedule")
    )
    assert "paths" not in str(events)
    assert document["permissions"] == {"contents": "read"}
    assert set(document["jobs"]) == {"gateseal"}
    assert job["name"] == (
        "${{ github.event_name == 'push' && 'gateseal-push' || 'gateseal' }}"
    )
    checkout, prepare, gate = job["steps"]
    assert checkout["uses"] == CHECKOUT
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    assert gate["env"]["GATESEAL_HEAD"] == "${{ github.sha }}"
    assert prepare["run"].rstrip().endswith('"$command/bin/gateseal" activate')
    assert (
        gate["run"]
        .rstrip()
        .endswith('"$RUNNER_TEMP/gateseal-command/bin/gateseal" gate "${scope[@]}"')
    )
    assert "TOKEN" not in str(gate["env"])


def test_preparation_pins_its_tools_and_the_locked_compiler():
    _, job = workflow()
    env = job["steps"][1]["env"]
    assert (env["GATESEAL_UV_URL"], env["GATESEAL_UV_SHA256"]) == (UV_URL, UV_SHA256)
    assert (env["GATESEAL_MISE_URL"], env["GATESEAL_MISE_SHA256"]) == (
        MISE_URL,
        MISE_SHA256,
    )
    assert env["GATESEAL_COMPILER"] == (
        f"gateseal @ {NETWORK.location} --hash=sha256:{NETWORK.sha256}"
    )
    assert env["GATESEAL_PYTHON"].startswith("3.14.")
    assert not any("TOKEN" in key or key.startswith("GIT_CONFIG") for key in env)
    assert "NETRC" not in job["steps"][1]["run"]


@pytest.mark.parametrize(
    "repository,helped",
    [
        (GIT, True),
        ("https://gitlab.com/team/gateseal-policy.git", False),
        ("ssh://git@github.com/team/gateseal-policy.git", False),
    ],
)
def test_only_a_github_https_policy_gets_a_scoped_token(repository, helped):
    source = git_source(repository, ".", "c" * 40, "v1.0.0")
    _, job = workflow(source)
    env = job["steps"][1]["env"]
    tokens = {key for key in env if "TOKEN" in key}
    assert tokens == ({"GATESEAL_POLICY_TOKEN"} if helped else set())
    if helped:
        assert env["GIT_CONFIG_COUNT"] == "3"
        assert env["GIT_CONFIG_VALUE_1"] == ""
        assert env["GIT_CONFIG_KEY_2"] == f"credential.{GIT}.helper"
    else:
        assert not any(key.startswith("GIT_CONFIG") for key in env)


def test_a_manifest_without_ci_has_no_workflow():
    assert WORKFLOW not in outputs(bundled(""))
    assert WORKFLOW not in outputs(bundled(""), LOCAL)


def test_the_lock_records_the_workflow():
    manifest = bundled()
    source = BundledSource(POLICY.release)
    selection = resolve(parse_manifest(manifest), POLICY)
    lock = compile_lock(manifest, source, POLICY, NETWORK, selection)
    assert WORKFLOW in lock["outputs"]


def test_a_local_wheel_compiler_cannot_run_in_ci():
    with pytest.raises(GatesealError) as failure:
        outputs(bundled(), LOCAL)
    assert failure.value.code == "ci_needs_network_compiler"
    assert "--compiler-source" in failure.value.next_step
    assert "--compiler-sha256" in failure.value.next_step


def test_the_workflow_passes_its_own_hygiene():
    data = outputs(bundled())[WORKFLOW]
    assert data.endswith(b"\n") and not data.endswith(b"\n\n")
    assert all(line == line.rstrip() for line in data.decode().splitlines())


@pytest.mark.parametrize(
    "table,code",
    [
        ('provider = "gitlab"\ndefault_branch = "main"', "unsupported_ci_provider"),
        ('provider = "github"', "invalid_field"),
        ('provider = "github"\ndefault_branch = "main"\nextra = 1', "invalid_field"),
        *(
            (f'provider = "github"\ndefault_branch = "{branch}"', "invalid_field")
            for branch in (
                "",
                "a..b",
                "/main",
                "main/",
                ".main",
                "main.",
                "main.lock",
                "a//b",
                "main branch",
                "main$x",
            )
        ),
    ],
)
def test_ci_names_a_known_provider_and_a_branch(table, code):
    with pytest.raises(GatesealError) as failure:
        parse_manifest(bundled(f"\n[ci]\n{table}\n"))
    assert failure.value.code == code


def test_render_writes_the_ci_table_configure_creates():
    text = render(("hygiene",), BundledSource(POLICY.release), Table("github", "main"))
    assert '\n[ci]\nprovider = "github"\ndefault_branch = "main"\n' in text.decode()
    assert parse_manifest(text).ci == Table("github", "main")


def test_each_stage_runs_in_a_pre_commit_stage():
    assert set(HOOK_STAGES) == set(Stage)


def definition(name):
    return files("gateseal").joinpath("data", "tools", f"{name}.tool.toml").read_text()


HISTORY = '\n[history]\nargs = ["dir", "--config", "{configuration}", "{range}"]\n'
RANGE_ARGUMENT = (
    '"--log-opts=--diff-merges=first-parent --text --no-textconv --no-ext-diff '
    '--no-color {range}",\n]'
)


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("ruff", "", HISTORY),
        (
            "betterleaks",
            '[history]\nargs = [\n  "git",',
            '[history]\nargs = [\n  "{x}",',
        ),
        ("betterleaks", RANGE_ARGUMENT, RANGE_ARGUMENT + '\nshell = "sh"'),
        ("betterleaks", RANGE_ARGUMENT, '"--log-opts",\n]'),
        ("betterleaks", RANGE_ARGUMENT, '"{range}", "{range}",\n]'),
    ],
    ids=[
        "history of a hook repository",
        "unknown placeholder",
        "unknown field",
        "no range",
        "two ranges",
    ],
)
def test_only_a_local_tool_scans_history_with_owned_arguments(name, old, new):
    text = definition(name)
    changed = text + new if not old else text.replace(old, new, 1)
    assert changed != text
    with pytest.raises(GatesealError) as failure:
        load_definition(name, changed.encode(), Origin.BUILT_IN)
    assert failure.value.code == "invalid_field"


def test_an_empty_history_scan_is_refused():
    text = definition("betterleaks")
    start = text.index("[history]")
    with pytest.raises(GatesealError) as failure:
        load_definition(
            "betterleaks",
            (text[:start] + "[history]\nargs = []\n").encode(),
            Origin.BUILT_IN,
        )
    assert failure.value.code == "invalid_field"
