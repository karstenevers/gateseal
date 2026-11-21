# Check Python tests

The bundled policy can run a uv project's tests at `git push` and in the CI gate. One test run under coverage.py feeds both the coverage report and the CRAP score of each function in a changed Python file. pytestmut then checks that the tests catch changes to the lines that the push changes.

## Select the checks

The repository needs a uv project, with `pyproject.toml` and `uv.lock` at its root. pytest must be in a dependency group that uv installs by default. For example:

<!-- python-tests-pyproject -->
```toml
[project]
name = "shop"
version = "0"
requires-python = ">=3.12"

[dependency-groups]
dev = ["pytest==9.2.0"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.coverage.run]
source = ["src", "tests"]

[tool.pytestmut]
paths = ["src/shop"]
```

The CRAP hook stops at a changed Python file that the coverage report does not measure. List each folder of Python code in coverage.py's `source`, the tests included. coverage.py then also measures a file that no test imports. To leave out other files, set the `exclude` choice of `python-crap` in `gateseal.toml`.

From the repository root, select the three capabilities and install the push hook:

```sh
gateseal configure \
  --capability python-tests \
  --capability python-crap \
  --capability python-mutation
gateseal setup --refresh
```

Without setup, run `pre-commit install` in place of the second command. Commit `gateseal.toml`, `gateseal.lock`, `.pre-commit-config.yaml` and `.gateseal/`. Select only the capabilities you want:

| Capability | Hooks | A push fails when |
| --- | --- | --- |
| `python-tests` | `clear coverage data`, `tests with coverage`, then `coverage report` | a test fails, or total coverage is under the project's `fail_under` |
| `python-crap` | `CRAP score` | a function in a changed Python file scores over 10. It needs `python-tests`. |
| `python-mutation` | `mutation tests` | no test catches a change to a changed line |

A function's CRAP score is its complexity squared times (1 − coverage) cubed, plus its complexity. A fully tested function scores its complexity. A function of complexity 4 without tests scores 20.

## What a push runs

The checks run only at `git push` and in the CI gate, never at a commit or in `gateseal run`. They start only when the push changes a Python file. A push that only deletes Python files starts none of them.

The hooks run in this order:

1. `clear coverage data` deletes the coverage data of an earlier push.
2. `tests with coverage` runs `coverage run --branch -m pytest`. A failing test fails the hook, and pytest's output names the test.
3. `coverage report` writes `.gateseal/reports/coverage.json`.
4. `CRAP score` reads that report and scores each function of the changed Python files.
5. `mutation tests` runs `pytestmut run --hook --min-score 100` on the lines that the push changes.

The checks read the files in your working tree, not the pushed commit. So check out the branch and commit your edits before you push. The CI gate checks the commit itself.

The reports are in `.gateseal/reports/`. The generated `.gateseal/reports/.gitignore` makes Git ignore them, so the repository's own `.gitignore` needs no entry. Each run overwrites them. `gateseal check` names each report that a hook reads, and the hook that writes it.

pytestmut runs its own tests and cannot use the coverage run. A push with all three capabilities runs the tests at least three times.

pytestmut 0.1.0 skips a changed file whose path has a character outside ASCII, such as `café.py`, because Git quotes that path. To include such files, run `git config core.quotePath false` in the clone. The CI gate's clone needs the same setting.

## Push time

The shared run saves two of three test runs. The table gives the median of 5 runs of each setup's hook commands. Each run checked a one-line change to gateseal's own code, which has 1,181 unit tests. pytest ran on one core of a 4-core Linux machine.

| Setup | Time |
| --- | --- |
| A test hook, a coverage hook and a CRAP hook that each run the tests | 164 s |
| `python-tests` and `python-crap`: one run under coverage.py | 58 s |
| `python-mutation`: pytestmut, in both setups | 123 s more |

pytestmut runs the tests twice: once to time them, and once to find the tests that run each line.

pytestmut bounds its own time at a push. By default it tries at most 40 code changes, first one on each changed line. After 180 seconds it tries no new change and passes. Change these limits in `[tool.pytestmut.diff]`:

```toml
[tool.pytestmut.diff]
max_mutants = 40
budget_seconds = 180.0
on_budget_exceeded = "fail"
```

The test run itself has no bound. Keep slow tests out of the default run with pytest's own settings, such as a marker that `addopts` deselects.

## Settings that stay in the repository

The policy sets the commands and the CRAP limit. The repository keeps these settings:

- **pytest.** `[tool.pytest.ini_options]` in `pyproject.toml`. The coverage run starts pytest without arguments.
- **coverage.py.** `[tool.coverage.*]`, such as `source`, `omit` and `fail_under`. The policy adds `--branch` and the data file. coverage.py measures a process that the tests start, such as with `concurrency = ["multiprocessing"]`, with the configuration alone. For such tests, also set `branch = true` and `data_file = ".gateseal/reports/coverage.data"` in `[tool.coverage.run]`.
- **pytestmut.** `[tool.pytestmut]`, such as `paths`, the pytest arguments, `[tool.pytestmut.diff]` and suppressions. `--min-score 100` replaces the minimum score of pytestmut's default policy. A per-path policy in `[tool.pytestmut]` keeps its own thresholds.
- **CRAP.** `.crap-baseline.toml`, the functions that may stay over the limit for now.

## Keep a function over the CRAP limit for now

List each such function in `.crap-baseline.toml` at the repository root, with its current score:

```toml
[baseline."src/shop/pricing.py"]
shipping = 20
```

The function passes while its score stays at or under its entry. The hook fails when the score rises over the entry. It also fails when the score falls within the limit, or when the function is gone. Then lower or remove the entry.

A change may lower or remove an entry, but not add or raise one. At a push and in a ranged gate, the hook compares the file with its version at the range's base. A file that the start lacks counts as adopted, so commit the first baseline on its own. A change to `.crap-baseline.toml` alone starts no check, so review each one.

## Run the tests once in CI

The CI gate runs the tests under coverage.py when the change has a Python file. If the repository's CI also has its own test job, the tests run twice for such a change.

The gate runs no test for a change without a Python file. Examples are a `uv.lock` update, new test data, and a change that only deletes Python files. Keep the repository's test job for those changes, and for other Python versions. Remove the job only if such a change may merge without a test run.

The gate's time includes the test run. A policy's [time budget](ci-gate.md#time-budget) warns when the gate takes longer.

Some gate runs check all tracked files, such as a weekly run or a new branch. pytestmut then has no range and tests no line. The first push to an empty remote has no range either. A pull request's gate has a range.
