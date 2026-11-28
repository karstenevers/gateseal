"""The bundled policy's TypeScript test checks, as a selection compiles them.

Vitest or Jest writes a coverage report at a push and in the gate, the CRAP
hook reads it, and StrykerJS runs from a generated configuration. No test
here starts a tool: test_typescript_tests drives them through real pushes.
"""

import json
import re

import pytest
from conftest import PIN
from gateseal.diagnostics import GatesealError
from gateseal.lock import compile_outputs
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy

CONFIG = ".pre-commit-config.yaml"
REPORT = ".gateseal/reports/coverage-final.json"
RUNNERS = {
    "typescript-vitest": ("vitest-coverage", "--coverage.reportsDirectory="),
    "typescript-jest": ("jest-coverage", "--coverageDirectory="),
}
AT_PUSH = ["pre-push", "manual"]


def compiled(*capabilities):
    """Return the files that configure writes for capabilities, and each hook."""
    policy = load_bundled_policy()
    source = BundledSource(policy.release)
    chosen = resolve(parse_manifest(render(capabilities, source)), policy)
    outputs = compile_outputs(chosen, PIN, source)
    repositories = json.loads(outputs[CONFIG])["repos"]
    hooks = [hook for found in repositories for hook in found["hooks"]]
    return outputs, {hook.get("alias", hook["id"]): hook for hook in hooks}


@pytest.mark.parametrize("runner", RUNNERS)
def test_the_crap_hook_reads_the_report_that_the_test_runner_writes(runner):
    writer, folder = RUNNERS[runner]
    outputs, hooks = compiled("typescript-crap", "typescript-mutation", runner)
    ordered = [name for name in hooks if not name.startswith("gateseal-")]
    assert ordered == [writer, "crap-score-typescript", "stryker"]
    assert {name: hooks[name]["stages"] for name in ordered} == dict.fromkeys(
        ordered, AT_PUSH
    )
    assert f"{folder}{REPORT.rpartition('/')[0]}" in hooks[writer]["args"]
    score = hooks["crap-score-typescript"]["args"]
    assert score[score.index("--coverage") + 1] == REPORT
    assert score[score.index("--config") + 1] == ".gateseal/crap-typescript.toml"
    assert outputs[".gateseal/crap-typescript.toml"].endswith(b"limit = 10\n")
    assert outputs[".gateseal/reports/.gitignore"] == b"*\n!.gitignore\n"
    assert hooks["stryker"]["args"][-2:] == ["run", ".gateseal/stryker-config.mjs"]


@pytest.mark.parametrize(
    ("path", "scored"),
    [
        ("src/pricing.ts", True),
        ("src/tests.ts", True),
        ("src/latest/pricing.ts", True),
        ("src/pricing.test.ts", False),
        ("src/pricing.spec.tsx", False),
        ("vite.config.ts", False),
        ("packages/shop/jest.config.js", False),
        ("src/config.ts", True),
        ("test/pricing.ts", False),
        ("packages/shop/tests/helper.ts", False),
        ("src/__tests__/pricing.ts", False),
        ("src/__mocks__/clock.ts", False),
        (".gateseal/stryker-config.mjs", False),
    ],
)
def test_the_crap_hook_scores_no_test_no_tool_configuration_and_no_generated_file(
    path, scored
):
    _, hooks = compiled("typescript-crap", "typescript-vitest")
    exclude = hooks["crap-score-typescript"]["exclude"]
    assert (re.search(exclude, path) is None) == scored


def test_the_crap_check_needs_a_capability_that_writes_the_coverage_report():
    with pytest.raises(GatesealError) as refusal:
        compiled("typescript-crap")
    assert (refusal.value.code, refusal.value.exit_code) == ("missing_report", 2)
    assert str(refusal.value) == (
        f"Hook crap-score-typescript reads {REPORT}, but no hook before it writes "
        "that report at a push and in the gate."
    )


def test_the_mutation_check_owns_the_configuration_that_stryker_reads():
    outputs, hooks = compiled("typescript-mutation")
    config = outputs[".gateseal/stryker-config.mjs"].decode()
    assert "break: 100" in config
    assert '".gateseal/reports/mutation.json"' in config
    assert "allowEmpty" not in config
    assert [name for name in hooks if not name.startswith("gateseal-")] == ["stryker"]
