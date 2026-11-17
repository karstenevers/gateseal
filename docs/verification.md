# Build and verify gateseal

This page is for contributors to gateseal. It shows how to build the package,
run its test layers, and which CI jobs run them.

**Related pages:**

- [What the tests prove](what-the-tests-prove.md) and
  [What the policy and tool tests prove](what-the-tool-tests-prove.md) say
  what the suite shows.
- [Tested platforms](tested-platforms.md) records the systems and prerequisite
  versions that the tests ran on.

## Build and test

**Before you start**, put Git 2.54.0, uv 0.13.6 and mise 2026.10.19 or later
on `PATH`. The package runs on CPython 3.11 and later, the floor of the
pre-commit version it names. Development and the integration layer use CPython
3.14.10, and CI also runs the unit layer on 3.11.

### Run the checks and tests

From the repository root, set up the development environment and run the
checks and the unit and integration layers:

```sh
uv sync --locked --python 3.14.10
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests -m 'not integration and not network'
uv run pytest tests -n auto -m 'not network'
```

The network layer reaches GitHub and PyPI. Run it with:

```sh
uv run pytest tests -n auto -m network
```

The Git policy test fetches a private fixture repository's copy of the
bundled policy with the token in `GATESEAL_POLICY_TOKEN`, and pytest skips it
when that variable is unset.

### Build a wheel

Replace `/absolute/path/dist` with an empty directory outside the checkout:

```sh
uv build --no-config --wheel --out-dir /absolute/path/dist .
```

### Test layers

The three pytest commands run these layers:

| Layer | What it runs | What it needs |
| --- | --- | --- |
| Unit | Every test not marked `integration` or `network`. It takes seconds. | Git and uv. |
| Integration | The unit layer and every test marked `integration`. conftest marks every test that uses the `installed` fixture. | Git, uv, and mise. |
| Network | The tests marked `network`. | HTTPS access to GitHub and PyPI, and `GATESEAL_POLICY_TOKEN` for the Git policy test. |

- **Workers.** `-n auto` runs one pytest-xdist worker per core, and each
  worker builds its own wheel and caches.
- **Temporary folders.** They go to pytest's default place.
  `--basetemp` or `PYTEST_DEBUG_TEMPROOT` choose another.

### The integration harness

- **Installed command.** The harness builds a wheel, installs it with required
  hashes into an isolated uv environment, and runs installed commands outside
  the source checkout.
- **Consumer repositories.** They are real clones and linked worktrees with
  spaces and non-ASCII characters in their paths.
- **Hook repositories.** pre-commit clones each hook repository once into
  the `installed` fixture's own home. That home's Git sends gateseal's
  repository through `url.<base>.insteadOf` to a stand-in that carries the
  package version's tag, and `with_git_config` adds a test's own Git settings
  after that.

### CI jobs

| Job | What it runs | When it runs |
| --- | --- | --- |
| `tests` | Ruff, strict Pyright, and the unit layer. | On pull requests, main pushes, and manually. |
| `tests (Python 3.11)` | The unit layer on CPython 3.11, the oldest supported Python. | On pull requests, main pushes, and manually. |
| `docs` | A strict build of the documentation site. | On pull requests, main pushes, and manually. |
| `integration` | The unit and integration layers on Linux and macOS. | On pull requests, main pushes, every Monday at 04:43 UTC, and manually. |
| `full-suite` | Every layer on Linux under coverage, with the private fixtures, then a check that flags functions that are both complex and poorly tested. | Like `integration`, except on pull requests from forks, which get no secrets. |
| `mutation-diff` | The [mutation check](#fix-a-failing-mutation-check) on the lines a pull request changes. | On pull requests. |
| `mutation-full` | Mutation testing on the whole unit layer. | Every Monday at 05:17 UTC, and manually. |

Before you present a gateseal pull request:

- Run the full suite locally.
- When it changes `src/gateseal`, also run the
  [mutation check](#fix-a-failing-mutation-check) locally.

## Fix a failing mutation check

`mutation-diff` uses
[pytestmut](https://github.com/martincjespersen/pytestmut) to change each line
that a pull request touches in `src/gateseal` one small step at a time, such
as `>` to `>=`. It then runs the unit tests that reach the change. A change
that no test notices is a survivor, and any survivor fails the job.

Run the same check from the repository root before you push:

```sh
uv run pytestmut run --diff "$(git merge-base origin/main HEAD)"
```

`uv run pytestmut show <id>` explains one mutant. For each survivor:

- **Add a test** that fails on the mutant, such as a case on the boundary of a
  comparison, or an assertion on the value a call returns or the state it
  changes.
- **Mark an equivalent mutant.** When no input can tell the mutant from the
  original, mark its line with the operator and the reason:

    ```python
    TIMEOUT = 60  # pytestmut: skip[num.shift] -- any timeout near a minute serves
    ```

The job also fails when the changed lines score below 90%. A mutant that only
the integration layer reaches counts as missed and lowers that score, so cover
new code in the unit layer. When only a test that downloads can reach a whole
function, list the function in `[tool.pytestmut.suppress]` in `pyproject.toml`
with a comment that gives the reason.
