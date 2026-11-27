"""Vitest or Jest runs once at a push for coverage and CRAP, beside StrykerJS.

A consumer with a Node project selects the bundled policy's typescript-vitest
or typescript-jest capability with typescript-crap and typescript-mutation.
Every push is a real git push to a bare repository, through the launcher that
pre-commit wrote. The first push installs Node, the test runner and StrykerJS
from the network, and pre-commit builds the CRAP hook from its repository on
GitHub.

The package manager that the install hook starts writes each project's
lockfile, so the project's own test runner is a real install.
"""

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import on_machine
from doc_examples import example
from installation import process, success

pytestmark = pytest.mark.integration

VITEST = ("typescript-vitest", "typescript-crap", "typescript-mutation")
JEST = ("typescript-jest", "typescript-crap", "typescript-mutation")
PACKAGE = example("typescript.md", "typescript-tests-package", "json")
VITEST_CONFIG = example("typescript.md", "typescript-tests-vitest-config", "ts")
PNPM_FIELDS = {"packageManager": "pnpm@10.28.0"}
JEST_PACKAGE = {
    "name": "shop",
    "private": True,
    "devDependencies": {
        "@types/jest": "29.5.14",
        "jest": "29.7.0",
        "ts-jest": "29.4.5",
        "typescript": "5.9.3",
    },
}
"""A project on an older Jest than the policy's, which compiles with ts-jest."""
JEST_CONFIG = """\
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  collectCoverageFrom: ["src/**"],
};
"""
JEST_TSCONFIG = '{ "compilerOptions": { "strict": true, "esModuleInterop": true } }\n'
PRICING = """\
export function total(cents: number[]): number {
  return cents.reduce((sum, each) => sum + each, 0);
}
"""
LEGACY = """\
export function legacy(cents: number): number {
  return cents * 3;
}
"""
"""Code that no test runs, which a push that leaves it alone does not mutate."""
TEST_PRICING = """\
import { expect, test } from "vitest";
import { total } from "../src/pricing";

test("total adds the cents", () => {
  expect(total([250, 100])).toBe(350);
});
"""
DISCOUNT = """
export function discount(cents: number): number {
  return cents - Math.floor(cents / 10);
}
"""
TEST_DISCOUNT = """\
import { expect, test } from "vitest";
import { discount } from "../src/pricing";

test("discount takes a tenth off", () => {
  expect(discount(1000)).toBe(900);
  expect(discount(99)).toBe(90);
});
"""
SHIPPING = """
export function shipping(cents: number, express: boolean, abroad: boolean): number {
  let fee = 500;
  if (express) {
    fee += 1000;
  }
  if (abroad) {
    fee *= 2;
  }
  if (cents > 10000) {
    fee = 0;
  }
  return fee;
}
"""
"""A function of complexity 4, whose CRAP score untested is 4 * 4 + 4 = 20."""
TEST_SHIPPING = """\
import { expect, test } from "vitest";
import { shipping } from "../src/pricing";

test("shipping depends on speed, place and total", () => {
  expect(shipping(0, false, false)).toBe(500);
  expect(shipping(0, true, false)).toBe(1500);
  expect(shipping(0, false, true)).toBe(1000);
  expect(shipping(10000, true, true)).toBe(3000);
  expect(shipping(10001, false, false)).toBe(0);
});
"""
FAILING = TEST_PRICING.replace("toBe(350)", "toBe(351)")
ISTANBUL = VITEST_CONFIG.replace("coverage: {", 'coverage: { provider: "istanbul",')
RUNNER = {VITEST: "Vitest with coverage", JEST: "Jest with coverage"}
CRAP = "CRAP score (TypeScript)"
MUTATION = "mutation tests (StrykerJS)"
NOTE = (
    "Hook crap-score-typescript reads .gateseal/reports/coverage-final.json, which "
    "vitest-coverage writes before it at a push and in the gate."
)


def with_jest(text):
    """Return a Vitest test file as Jest reads it, with its global functions."""
    return text.replace('import { expect, test } from "vitest";\n', "")


def status(name, shown):
    """Return the status pre-commit printed on the line of hook name, or None."""
    found = re.search(
        rf"^{re.escape(name)}\.+(\(no files to check\))?(\w+)$", shown, re.M
    )
    return found and found[2]


@dataclass(frozen=True)
class Shop:
    """A clone of the shop project and the bare repository it pushes to."""

    clone: Path
    remote: Path
    env: dict[str, str]
    capabilities: tuple[str, ...] = VITEST

    def git(self, *arguments):
        return success(["git", *arguments], self.clone, self.env).stdout

    def commit(self, files, message="Change"):
        """Write files, commit them and return the new head."""
        for name, text in files.items():
            path = self.clone / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def appended(self, name, text):
        return (self.clone / name).read_text() + text

    def push(self, *refspec, env=None):
        pushed = process(
            ["git", "push", "origin", *(refspec or ["main"])],
            self.clone,
            self.env | (env or {}),
        )
        return pushed.returncode, pushed.stdout + pushed.stderr

    def pushed(self, branch="main"):
        found = process(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            self.remote,
        )
        return found.stdout.strip() or None

    def statuses(self, shown):
        names = (RUNNER[self.capabilities], CRAP, MUTATION)
        return [status(name, shown) for name in names]


ASK_PACKAGE_MANAGER = (
    "import json, pathlib\n"
    "from gateseal.node_packages import on_pinned_node\n"
    "from gateseal.tools.package_managers import package_manager\n"
    "command, env = on_pinned_node(package_manager(pathlib.Path.cwd(), ''))\n"
    "print(json.dumps([command, env['PATH']]))"
)


def write_lockfile(shop, manager):
    """Write the lockfile with the package manager that the install hook starts."""
    asked = success([sys.executable, "-c", ASK_PACKAGE_MANAGER], shop.clone, shop.env)
    command, path = json.loads(asked.stdout)
    flags = {
        "npm": ["install", "--package-lock-only", "--ignore-scripts", "--no-audit"],
        "pnpm": ["install", "--lockfile-only", "--ignore-scripts"],
    }
    success([*command, *flags[manager]], shop.clone, shop.env | {"PATH": path})


def adopted(installed, root, files, capabilities=VITEST, manager="npm"):
    """Adopt capabilities in a project of files, and push the adoption."""
    remote = root / "remote.git"
    shop = Shop(root / "clone", remote, installed.environment, capabilities)
    with on_machine(installed):
        success(["git", "init", "--quiet", "--bare", "-b", "main", remote], root)
        success(["git", "clone", "--quiet", remote, shop.clone], root)
        shop.git("config", "user.name", "gateseal test")
        shop.git("config", "user.email", "gateseal@example.invalid")
        lockfile = {"npm": "package-lock.json", "pnpm": "pnpm-lock.yaml"}[manager]
        for name, text in (
            {".gitignore": "node_modules/\n", lockfile: ""} | files
        ).items():
            (shop.clone / name).parent.mkdir(parents=True, exist_ok=True)
            (shop.clone / name).write_text(text)
        flags = [option for name in capabilities for option in ("--capability", name)]
        installed.cli(shop.clone, "configure", *flags)
        installed.cli(shop.clone, "setup", "--refresh")
        write_lockfile(shop, manager)
        shop.git("add", "-A")
        shop.git("commit", "--quiet", "-m", "Adopt the test checks")
        code, shown = shop.push()
        assert code == 0, shown
        assert shop.statuses(shown) == ["Passed"] * 3, shown
    return shop


VITEST_FILES = {
    "package.json": PACKAGE,
    "vitest.config.ts": VITEST_CONFIG,
    "src/pricing.ts": PRICING,
    "src/legacy.ts": LEGACY,
    "test/pricing.test.ts": TEST_PRICING,
}


@pytest.fixture(scope="module")
def module_shop(installed, tmp_path_factory):
    """An npm project on Vitest that adopted the three capabilities."""
    root = tmp_path_factory.mktemp("vitest consumer ü").resolve()
    return adopted(installed, root, VITEST_FILES)


@pytest.fixture(scope="module")
def module_jest_shop(installed, tmp_path_factory):
    """An npm project on Jest and ts-jest that adopted the three capabilities."""
    root = tmp_path_factory.mktemp("jest consumer ü").resolve()
    files = {
        "package.json": json.dumps(JEST_PACKAGE, indent=2) + "\n",
        "jest.config.js": JEST_CONFIG,
        "tsconfig.json": JEST_TSCONFIG,
        "src/pricing.ts": PRICING,
        "src/legacy.ts": LEGACY,
        "test/pricing.test.ts": with_jest(TEST_PRICING),
    }
    return adopted(installed, root, files, JEST)


def copied(shop, tmp_path):
    """Return a copy of the adopted clone and its remote.

    The clone's folder has brackets, which StrykerJS reads as a pattern.
    """
    copy = Shop(
        tmp_path / "clone [ü]", tmp_path / "remote.git", shop.env, shop.capabilities
    )
    shutil.copytree(shop.remote, copy.remote)
    shutil.copytree(shop.clone, copy.clone, symlinks=True)
    copy.git("remote", "set-url", "origin", str(copy.remote))
    return copy


@pytest.fixture
def shop(module_shop, tmp_path):
    return copied(module_shop, tmp_path)


@pytest.fixture
def jest_shop(module_jest_shop, tmp_path):
    return copied(module_jest_shop, tmp_path)


def gate(installed, shop, base, head, code):
    """Run the gate on base..head and return the result and each hook's status."""
    shop.git("checkout", "--quiet", head)
    result = installed.cli(
        shop.clone, "gate", "--base", base, "--head", head, code=code
    )
    entries = [d for d in result["diagnostics"] if d["category"] == "hook"]
    return result, {entry["hook"]: entry["status"] for entry in entries}


def details(result):
    """Return every message of result, pre-commit's output among them."""
    return "\n".join(d["message"] for d in result["diagnostics"])


def survivors(shown):
    """Return the file and line of each mutant that StrykerJS says no test killed."""
    found = r"^\[(?:Survived|NoCoverage)\] \w+\n(\S.*:\d+):\d+$"
    return set(re.findall(found, shown, re.M))


def measured_by_v8(shop):
    """Say whether Vitest's v8 provider wrote the report: only it adds meta."""
    report = shop.clone / ".gateseal/reports/coverage-final.json"
    return ["meta" in entry for entry in json.loads(report.read_text()).values()]


def test_the_adoption_writes_the_ignore_file_and_check_names_the_report(
    installed, module_shop
):
    committed = module_shop.git("ls-files").split("\n")
    assert ".gateseal/reports/.gitignore" in committed
    assert ".gateseal/stryker-config.mjs" in committed
    rules = module_shop.clone / ".gateseal/crap-typescript.toml"
    assert rules.read_text().endswith("limit = 10\n")
    check = installed.cli(module_shop.clone, "check")
    assert NOTE in [d["message"] for d in check["diagnostics"]]


def test_a_push_runs_the_tests_before_the_hooks_that_read_their_report(shop):
    head = shop.commit(
        {
            "src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT),
            "test/discount.test.ts": TEST_DISCOUNT,
        }
    )
    code, shown = shop.push()
    assert code == 0, shown
    assert shop.statuses(shown) == ["Passed"] * 3, shown
    order = [shown.index(name) for name in (RUNNER[VITEST], CRAP, MUTATION)]
    assert order == sorted(order)
    assert shop.pushed() == head
    reports = shop.clone / ".gateseal/reports"
    assert sorted(path.name for path in reports.iterdir()) == [
        ".gitignore",
        "coverage-final.json",
        "mutation.json",
    ]
    assert shop.git("status", "--porcelain", "--ignored=no") == ""
    assert measured_by_v8(shop) == [True, True]


def test_a_failing_test_stops_the_push_and_the_gate_naming_it(installed, shop):
    base = shop.git("rev-parse", "HEAD").strip()
    head = shop.commit({"test/pricing.test.ts": FAILING})
    code, shown = shop.push()
    assert code == 1, shown
    assert status(RUNNER[VITEST], shown) == "Failed", shown
    plain = re.sub(r"\x1b\[[\d;]*m", "", shown)
    assert "FAIL  test/pricing.test.ts > total adds the cents" in plain, shown
    assert shop.pushed() == base
    result, hooks = gate(installed, shop, base, head, 1)
    assert result["code"] == "checks_failed"
    assert hooks["vitest-coverage"] == "failed"
    assert "test/pricing.test.ts > total adds the cents" in details(result)


def test_an_untested_function_stops_the_push_naming_its_mutants(installed, shop):
    base = shop.git("rev-parse", "HEAD").strip()
    untested = shop.commit(
        {"src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT)}
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert shop.statuses(shown) == ["Passed", "Passed", "Failed"], shown
    assert survivors(shown) == {"src/pricing.ts:5", "src/pricing.ts:6"}, shown
    assert shop.pushed() == base
    result, hooks = gate(installed, shop, base, untested, 1)
    assert hooks["stryker"] == "failed"
    assert hooks["crap-score-typescript"] == "passed"
    assert survivors(details(result)) == survivors(shown)
    shop.git("checkout", "--quiet", "main")
    tested = shop.commit({"test/discount.test.ts": TEST_DISCOUNT})
    code, shown = shop.push()
    assert code == 0, shown
    assert status(MUTATION, shown) == "Passed"
    result, hooks = gate(installed, shop, base, tested, 0)
    assert result["code"] == "checks_passed"
    assert hooks["stryker"] == "passed"


def test_a_new_branch_mutates_only_the_lines_it_adds(shop):
    shop.git("switch", "--quiet", "-c", "feature")
    shop.commit({"src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT)})
    shop.commit({"src/pricing.ts": shop.appended("src/pricing.ts", "\n// A note.\n")})
    code, shown = shop.push("feature")
    assert code == 1, shown
    assert survivors(shown) == {"src/pricing.ts:5", "src/pricing.ts:6"}, shown
    assert shop.pushed("feature") is None
    shop.commit({"test/discount.test.ts": TEST_DISCOUNT})
    code, shown = shop.push("feature", env={"GIT_DIFF_OPTS": "--unified=3"})
    assert code == 0, shown
    assert status(MUTATION, shown) == "Passed"
    report = json.loads((shop.clone / ".gateseal/reports/mutation.json").read_text())
    assert list(report["files"]) == ["src/pricing.ts"]
    lines = {
        m["location"]["start"]["line"]
        for m in report["files"]["src/pricing.ts"]["mutants"]
    }
    assert lines == {5, 6}


def test_an_added_line_that_looks_like_a_file_header_hides_no_later_change(shop):
    """Git shows the added line "++ count;" as "+++ count;", like a file header."""
    counter = "let count = 0;\n++ count;\n\n"
    changed = counter + shop.appended("src/pricing.ts", DISCOUNT)
    shop.commit({"src/pricing.ts": changed})
    code, shown = shop.push()
    assert code == 1, shown
    assert {"src/pricing.ts:8", "src/pricing.ts:9"} <= survivors(shown), shown


def test_neither_check_starts_at_a_commit_in_run_or_without_a_code_file(
    installed, shop
):
    shop.commit({"src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT)})
    names = (RUNNER[VITEST], CRAP, MUTATION)
    amended = process(["git", "commit", "--amend", "-m", "Again"], shop.clone, shop.env)
    assert amended.returncode == 0, amended.stdout + amended.stderr
    assert not any(name in amended.stdout + amended.stderr for name in names)
    run = installed.cli(shop.clone, "run")
    ran = {d["hook"] for d in run["diagnostics"] if d["category"] == "hook"}
    assert not ran & {"vitest-coverage", "crap-score-typescript", "stryker"}
    shop.git("reset", "--quiet", "--hard", "origin/main")
    shop.commit({"README.md": "The shop.\n"})
    code, shown = shop.push()
    assert code == 0, shown
    assert shop.statuses(shown) == ["Skipped"] * 3, shown


def test_a_function_over_the_crap_limit_fails_until_it_is_tested(installed, shop):
    base = shop.git("rev-parse", "HEAD").strip()
    untested = shop.commit(
        {"src/pricing.ts": shop.appended("src/pricing.ts", SHIPPING)}
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert status(CRAP, shown) == "Failed", shown
    result, hooks = gate(installed, shop, base, untested, 1)
    assert hooks["crap-score-typescript"] == "failed"
    assert re.search(r"src/pricing\.ts\s+shipping\s+20\.00", details(result))
    assert "1 over the limit of 10" in details(result)
    shop.git("checkout", "--quiet", "main")
    tested = shop.commit({"test/shipping.test.ts": TEST_SHIPPING})
    result, hooks = gate(installed, shop, base, tested, 0)
    assert result["code"] == "checks_passed"
    assert hooks["crap-score-typescript"] == "passed"
    assert hooks["stryker"] == "passed"


def test_a_change_that_adds_a_baseline_entry_fails(shop):
    shop.commit({".crap-baseline.toml": "[baseline]\n"}, "Adopt a CRAP baseline")
    code, shown = shop.push()
    assert code == 0, shown
    shop.commit(
        {
            "src/pricing.ts": shop.appended("src/pricing.ts", SHIPPING),
            "test/shipping.test.ts": TEST_SHIPPING.replace("(10001", "(10002"),
            ".crap-baseline.toml": '[baseline."src/pricing.ts"]\nshipping = 20\n',
        }
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert status(CRAP, shown) == "Failed", shown
    assert (
        "src/pricing.ts shipping: a change may not add an entry to "
        ".crap-baseline.toml." in shown
    )


def test_the_crap_hook_scores_a_report_of_vitest_s_istanbul_provider(shop):
    shop.commit(
        {
            "vitest.config.ts": ISTANBUL,
            "src/pricing.ts": shop.appended("src/pricing.ts", SHIPPING),
        }
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert measured_by_v8(shop) == [False, False]
    assert status(CRAP, shown) == "Failed", shown
    assert re.search(r"src/pricing\.ts\s+shipping\s+20\.00", shown)
    shop.commit({"test/shipping.test.ts": TEST_SHIPPING})
    code, shown = shop.push()
    assert code == 0, shown
    assert shop.statuses(shown) == ["Passed"] * 3, shown


def test_a_stryker_configuration_of_the_repository_lowers_no_threshold(shop):
    """The repository says where its code is, and the policy what passes."""
    own = {
        "mutate": ["./src/pricing.ts"],
        "thresholds": {"high": 0, "low": 0, "break": 0},
        "allowEmpty": True,
        "mutator": {"excludedMutations": ["ArithmeticOperator", "BlockStatement"]},
    }
    shop.commit(
        {
            "stryker.config.json": json.dumps(own),
            "src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT),
            "src/legacy.ts": LEGACY.replace("* 3", "* 4"),
        }
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert status(MUTATION, shown) == "Failed", shown
    assert survivors(shown) == {"src/pricing.ts:5", "src/pricing.ts:6"}, shown


def test_a_changed_file_with_an_odd_name_is_mutated(shop):
    """StrykerJS reads each name as a pattern, in which a bracket has a meaning."""
    name = "src/price list (old) [1] ü*.ts"
    test = TEST_DISCOUNT.replace("../src/pricing", f"../{name[:-3]}")
    weak = re.sub(r"  expect.*\n  expect.*\n", "  expect(discount(0)).toBe(0);\n", test)
    notes = {'A "draft".md': "Notes.\n", "docs/a\tb \\c {d}.md": "Notes.\n"}
    shop.commit({name: DISCOUNT, "test/odd.test.ts": weak} | notes)
    code, shown = shop.push()
    assert code == 1, shown
    assert status(MUTATION, shown) == "Failed", shown
    assert survivors(shown) == {f"{name}:3"}, shown


@pytest.mark.parametrize("name", ["src/{a,b}.ts", "src/a\\b.ts"])
def test_a_changed_file_that_stryker_cannot_name_stops_the_push(shop, name):
    shop.commit({name: DISCOUNT})
    code, shown = shop.push()
    assert code == 1, shown
    assert status(MUTATION, shown) == "Failed", shown
    assert (
        f"StrykerJS cannot take the file name {name}. Rename the file "
        'without {, }, " and \\.' in shown
    )


def test_a_forced_push_mutates_only_the_lines_of_its_own_commits(shop):
    """A commit that only the remote branch has is no change of the push."""
    start = shop.git("rev-parse", "HEAD").strip()
    shop.commit({"src/legacy.ts": LEGACY.replace("* 3", "* 4")})
    code, shown = shop.push("--no-verify", "main")
    assert code == 0, shown
    shop.git("reset", "--quiet", "--hard", start)
    shop.commit({"src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT)})
    code, shown = shop.push("--force", "main")
    assert code == 1, shown
    assert survivors(shown) == {"src/pricing.ts:5", "src/pricing.ts:6"}, shown


def test_a_file_that_git_does_not_diff_is_mutated_all_the_same(shop):
    shop.commit(
        {
            ".gitattributes": "*.ts -diff\n",
            "src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT),
        }
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert survivors(shown) == {"src/pricing.ts:5", "src/pricing.ts:6"}, shown


def test_a_pnpm_project_runs_the_same_checks(installed, tmp_path):
    package = json.dumps(json.loads(PACKAGE) | PNPM_FIELDS, indent=2) + "\n"
    root = tmp_path.resolve()
    shop = adopted(
        installed, root, VITEST_FILES | {"package.json": package}, manager="pnpm"
    )
    with on_machine(installed):
        shop.commit({"src/pricing.ts": shop.appended("src/pricing.ts", DISCOUNT)})
        code, shown = shop.push()
        assert code == 1, shown
        assert shop.statuses(shown) == ["Passed", "Passed", "Failed"], shown
        assert survivors(shown) == {"src/pricing.ts:5", "src/pricing.ts:6"}, shown


def test_jest_writes_the_report_that_the_crap_hook_scores(installed, jest_shop):
    base = jest_shop.git("rev-parse", "HEAD").strip()
    untested = jest_shop.commit(
        {"src/pricing.ts": jest_shop.appended("src/pricing.ts", SHIPPING)}
    )
    code, shown = jest_shop.push()
    assert code == 1, shown
    assert jest_shop.statuses(shown) == ["Passed", "Failed", "Failed"], shown
    assert re.search(r"src/pricing\.ts\s+shipping\s+20\.00", shown)
    assert "src/pricing.ts:5" in survivors(shown)
    _, hooks = gate(installed, jest_shop, base, untested, 1)
    assert hooks["jest-coverage"] == "passed"
    assert hooks["crap-score-typescript"] == "failed"
    assert hooks["stryker"] == "failed"
    jest_shop.git("checkout", "--quiet", "main")
    jest_shop.commit({"test/shipping.test.ts": with_jest(TEST_SHIPPING)})
    code, shown = jest_shop.push()
    assert code == 0, shown
    assert jest_shop.statuses(shown) == ["Passed"] * 3, shown


def test_a_c8_report_fails_with_a_message_that_names_vitest_and_jest(module_shop):
    """No bundled hook writes one, so the hook runs on the report with c8's mark."""
    report = module_shop.clone / ".gateseal/reports/coverage-final.json"
    entries = json.loads(report.read_text())
    report.write_text(json.dumps({k: v | {"all": True} for k, v in entries.items()}))
    ran = process(
        ["pre-commit", "run", "crap-score-typescript", "--hook-stage", "pre-push"]
        + ["--files", "src/pricing.ts"],
        module_shop.clone,
        module_shop.env,
    )
    assert ran.returncode == 1, ran.stdout + ran.stderr
    assert (
        "has a c8 entry for src/pricing.ts. c8 counts whole lines. Use Vitest, or "
        "Jest with its default coverage provider" in ran.stdout
    )
