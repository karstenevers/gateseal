"""The tests run once at a push for coverage and CRAP, beside mutation tests.

A consumer with a uv project selects the bundled policy's python-tests,
python-crap and python-mutation capabilities. Every push is a real git push to
a bare repository, through the launcher that pre-commit wrote. The first push
installs coverage.py, pytestmut and pytest from PyPI, and pre-commit builds
the CRAP hook from its repository on GitHub.
"""

import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import BUNDLED_TOOLS, on_machine
from doc_examples import example
from installation import process, success

pytestmark = pytest.mark.integration

CAPABILITIES = ("python-tests", "python-crap", "python-mutation")
PYPROJECT = example("python-tests.md", "python-tests-pyproject")
PRICING = """\
def total(cents: list[int]) -> int:
    return sum(cents)
"""
LEGACY = """\
def legacy(cents: int) -> int:
    return cents * 3
"""
"""Code that no test runs, which a push that leaves it alone does not mutate."""
TEST_PRICING = """\
from shop.pricing import total


def test_total():
    assert total([250, 100]) == 350
"""
DISCOUNT = """
def discount(cents: int) -> int:
    return cents - cents // 10
"""
TEST_DISCOUNT = """
from shop.pricing import discount


def test_discount():
    assert discount(1000) == 900
    assert discount(99) == 90
"""
SHIPPING = """
def shipping(cents: int, express: bool, abroad: bool) -> int:
    fee = 500
    if express:
        fee += 1000
    if abroad:
        fee *= 2
    if cents > 10000:
        fee = 0
    return fee
"""
"""A function of complexity 4, whose CRAP score untested is 4 * 4 + 4 = 20."""
TEST_SHIPPING = """
from shop.pricing import shipping


def test_shipping():
    assert shipping(0, False, False) == 500
    assert shipping(0, True, False) == 1500
    assert shipping(0, False, True) == 1000
    assert shipping(10000, True, True) == 3000
    assert shipping(10001, False, False) == 0
"""
FAILING = TEST_PRICING.replace("== 350", "== 351")
HOOKS = (
    "clear coverage data",
    "tests with coverage",
    "coverage report",
    "CRAP score",
    "mutation tests",
)
NOTE = (
    "Hook crap-score-python reads .gateseal/reports/coverage.json, which "
    "coverage-json writes before it at a push and in the gate."
)


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

    def git(self, *arguments):
        return success(["git", *arguments], self.clone, hook_environment()).stdout

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

    def push(self, *refspec):
        pushed = process(
            ["git", "push", "origin", *(refspec or ["main"])],
            self.clone,
            hook_environment(),
        )
        return pushed.returncode, pushed.stdout + pushed.stderr

    def pushed(self, branch="main"):
        found = process(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            self.remote,
        )
        return found.stdout.strip() or None


def hook_environment(environment=None):
    """Return environment, os.environ by default, without coverage's variables.

    A suite that runs under coverage would otherwise start measuring in the
    consumer's own test processes.
    """
    return {
        k: v
        for k, v in (os.environ if environment is None else environment).items()
        if not k.startswith("COVERAGE_")
    }


@pytest.fixture(scope="module")
def module_shop(installed, tmp_path_factory):
    """Adopt the three capabilities and push the adoption."""
    root = tmp_path_factory.mktemp("shop consumer ü").resolve()
    remote = root / "remote.git"
    with on_machine(installed):
        success(["git", "init", "--quiet", "--bare", "-b", "main", remote], root)
        success(["git", "clone", "--quiet", remote, root / "clone"], root)
        shop = Shop(root / "clone", remote)
        shop.git("config", "user.name", "gateseal test")
        shop.git("config", "user.email", "gateseal@example.invalid")
        files = {
            ".gitignore": "__pycache__/\n",
            "pyproject.toml": PYPROJECT,
            "src/shop/__init__.py": "",
            "src/shop/pricing.py": PRICING,
            "src/shop/legacy.py": LEGACY,
            "tests/test_pricing.py": TEST_PRICING,
        }
        for name, text in files.items():
            (shop.clone / name).parent.mkdir(parents=True, exist_ok=True)
            (shop.clone / name).write_text(text)
        success(["uv", "lock"], shop.clone, installed.environment)
        flags = [option for name in CAPABILITIES for option in ("--capability", name)]
        installed.cli(shop.clone, "configure", *flags)
        installed.cli(shop.clone, "setup", "--refresh")
        shop.git("add", "-A")
        shop.git("commit", "--quiet", "-m", "Adopt the test checks")
        code, shown = shop.push()
        assert code == 0, shown
    return shop


@pytest.fixture
def shop(module_shop, tmp_path):
    """A copy of the adopted clone and its remote."""
    copy = Shop(tmp_path / "clone ü", tmp_path / "remote.git")
    shutil.copytree(module_shop.remote, copy.remote)
    shutil.copytree(module_shop.clone, copy.clone, symlinks=True)
    copy.git("remote", "set-url", "origin", str(copy.remote))
    return copy


def gate(installed, shop, base, head, code):
    """Run the gate on base..head and return each hook's status and the output.

    The gate runs with the push's environment. The installed command's uv
    cache sits in a folder whose name has a space. uv then writes the console
    script of a --with package with a shebang that cannot run, and starts
    coverage without the project's packages.
    """
    shop.git("checkout", "--quiet", head)
    result = installed.cli(
        shop.clone,
        "gate",
        "--base",
        base,
        "--head",
        head,
        code=code,
        env=hook_environment(),
    )
    entries = [d for d in result["diagnostics"] if d["category"] == "hook"]
    return result, {entry["hook"]: entry["status"] for entry in entries}


def details(result):
    """Return every message of result, pre-commit's output among them."""
    return "\n".join(d["message"] for d in result["diagnostics"])


def test_the_adoption_writes_the_ignore_file_and_check_names_the_report(
    installed, module_shop
):
    committed = module_shop.git("ls-files").split("\n")
    assert ".gateseal/reports/.gitignore" in committed
    assert ".gateseal/crap.toml" in committed
    assert (
        (module_shop.clone / ".gateseal/crap.toml").read_text().endswith("limit = 10\n")
    )
    check = installed.cli(module_shop.clone, "check")
    assert NOTE in [d["message"] for d in check["diagnostics"]]
    status = installed.cli(module_shop.clone, "status")
    assert NOTE in [d["message"] for d in status["diagnostics"]]
    coverage = BUNDLED_TOOLS["coverage"].pin
    assert (
        f"--with coverage=={coverage} -- coverage run --branch"
        in (module_shop.clone / ".pre-commit-config.yaml").read_text()
    )


def test_a_push_runs_the_tests_once_before_the_hooks_that_read_their_report(
    shop,
):
    head = shop.commit(
        {
            "src/shop/pricing.py": shop.appended("src/shop/pricing.py", DISCOUNT),
            "tests/test_discount.py": TEST_DISCOUNT,
        }
    )
    code, shown = shop.push()
    assert code == 0, shown
    assert [status(name, shown) for name in HOOKS] == ["Passed"] * len(HOOKS), shown
    assert [shown.index(name) for name in HOOKS] == sorted(
        shown.index(name) for name in HOOKS
    )
    assert shown.count("tests with coverage") == 1
    assert shop.pushed() == head
    reports = shop.clone / ".gateseal/reports"
    assert sorted(path.name for path in reports.iterdir()) == [
        ".gitignore",
        "coverage.data",
        "coverage.json",
    ]
    assert shop.git("status", "--porcelain", "--ignored=no") == ""


def test_a_push_reports_the_coverage_of_its_own_test_run_alone(shop):
    """coverage.py would combine an earlier push's parallel data into the report."""
    pyproject = (shop.clone / "pyproject.toml").read_text()
    shop.commit(
        {
            "pyproject.toml": pyproject.replace(
                "[tool.coverage.run]\n", "[tool.coverage.run]\nparallel = true\n"
            ),
            "src/shop/pricing.py": shop.appended("src/shop/pricing.py", DISCOUNT),
            "tests/test_discount.py": TEST_DISCOUNT,
        }
    )
    code, shown = shop.push()
    assert code == 0, shown
    shop.commit({"tests/test_discount.py": "def test_nothing():\n    pass\n"})
    code, shown = shop.push()
    assert code == 0, shown
    report = json.loads((shop.clone / ".gateseal/reports/coverage.json").read_text())
    assert report["files"]["src/shop/pricing.py"]["missing_lines"] == [5]


def test_a_failing_test_stops_the_push_and_the_gate_naming_it(installed, shop):
    base = shop.git("rev-parse", "HEAD").strip()
    head = shop.commit({"tests/test_pricing.py": FAILING})
    code, shown = shop.push()
    assert code == 1, shown
    assert status("tests with coverage", shown) == "Failed", shown
    assert "FAILED tests/test_pricing.py::test_total" in shown
    assert shop.pushed() == base
    result, hooks = gate(installed, shop, base, head, 1)
    assert result["code"] == "checks_failed"
    assert hooks["coverage-run"] == "failed"
    assert "FAILED tests/test_pricing.py::test_total" in details(result)


def test_an_untested_function_stops_the_push_naming_its_mutants(installed, shop):
    base = shop.git("rev-parse", "HEAD").strip()
    untested = shop.commit(
        {"src/shop/pricing.py": shop.appended("src/shop/pricing.py", DISCOUNT)}
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert status("mutation tests", shown) == "Failed", shown
    assert f"scope: lines changed against {base}" in shown
    assert re.search(r"src/shop/pricing\.py:5\b", shown), shown
    assert not re.search(r"src/shop/legacy\.py:\d", shown), shown
    assert status("CRAP score", shown) == "Passed", shown
    assert shop.pushed() == base
    _, hooks = gate(installed, shop, base, untested, 1)
    assert hooks["pytestmut"] == "failed"
    assert hooks["crap-score-python"] == "passed"
    shop.git("checkout", "--quiet", "main")
    tested = shop.commit({"tests/test_discount.py": TEST_DISCOUNT})
    code, shown = shop.push()
    assert code == 0, shown
    assert status("mutation tests", shown) == "Passed"
    result, hooks = gate(installed, shop, base, tested, 0)
    assert result["code"] == "checks_passed"
    assert hooks["pytestmut"] == "passed"


def test_a_new_branch_mutates_only_the_lines_it_adds(shop):
    main = shop.git("rev-parse", "HEAD").strip()
    shop.git("switch", "--quiet", "-c", "feature")
    shop.commit({"src/shop/pricing.py": shop.appended("src/shop/pricing.py", DISCOUNT)})
    code, shown = shop.push("feature")
    assert code == 1, shown
    assert f"scope: lines changed against {main}" in shown
    assert re.search(r"src/shop/pricing\.py:5\b", shown), shown
    assert not re.search(r"src/shop/legacy\.py:\d", shown), shown
    assert shop.pushed("feature") is None
    shop.commit({"tests/test_discount.py": TEST_DISCOUNT})
    code, shown = shop.push("feature")
    assert code == 0, shown
    assert status("mutation tests", shown) == "Passed"


def test_neither_check_starts_at_a_commit_in_run_or_without_a_python_file(
    installed, shop
):
    committed = process(
        ["git", "commit", "--allow-empty", "-m", "Empty"],
        shop.clone,
        hook_environment(),
    )
    assert committed.returncode == 0, committed.stdout + committed.stderr
    assert not any(name in committed.stdout + committed.stderr for name in HOOKS)
    run = installed.cli(shop.clone, "run")
    ran = {d["hook"] for d in run["diagnostics"] if d["category"] == "hook"}
    assert not ran & {
        "coverage-erase",
        "coverage-run",
        "coverage-json",
        "crap-score-python",
        "pytestmut",
    }
    shop.commit({"README.md": "The shop.\n"})
    code, shown = shop.push()
    assert code == 0, shown
    assert [status(name, shown) for name in HOOKS] == ["Skipped"] * len(HOOKS), shown


def test_a_function_over_the_crap_limit_fails_until_it_is_tested(installed, shop):
    base = shop.git("rev-parse", "HEAD").strip()
    untested = shop.commit(
        {"src/shop/pricing.py": shop.appended("src/shop/pricing.py", SHIPPING)}
    )
    result, hooks = gate(installed, shop, base, untested, 1)
    assert hooks["crap-score-python"] == "failed"
    assert re.search(r"src/shop/pricing\.py\s+shipping\s+20\.00", details(result))
    assert "1 over the limit of 10" in details(result)
    shop.git("checkout", "--quiet", "main")
    tested = shop.commit({"tests/test_shipping.py": TEST_SHIPPING})
    result, hooks = gate(installed, shop, base, tested, 0)
    assert result["code"] == "checks_passed"
    assert hooks["crap-score-python"] == "passed"
    assert hooks["pytestmut"] == "passed"


def test_a_change_that_adds_a_baseline_entry_fails(shop):
    shop.commit({".crap-baseline.toml": "[baseline]\n"}, "Adopt a CRAP baseline")
    code, shown = shop.push()
    assert code == 0, shown
    shop.commit(
        {
            "src/shop/pricing.py": shop.appended("src/shop/pricing.py", SHIPPING),
            ".crap-baseline.toml": '[baseline."src/shop/pricing.py"]\nshipping = 20\n',
        }
    )
    code, shown = shop.push()
    assert code == 1, shown
    assert status("CRAP score", shown) == "Failed", shown
    assert (
        "src/shop/pricing.py shipping: a change may not add an entry to "
        ".crap-baseline.toml." in shown
    )
