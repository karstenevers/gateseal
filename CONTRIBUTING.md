# Contributing to gateseal

A fix, a tool or a capability is welcome when it comes with its tests and
documentation. This page says which changes fit, how to run the checks, and
what a pull request needs.

## What fits

gateseal compiles a reviewed policy into native configuration and leaves the
rest to existing tools. These changes fit:

- **A fix.** Bring a test that fails without it.
- **A capability or a tool of a kind gateseal already implements.** Bring the
  policy entry, the tool definition, tests and documentation.
  [Add a tool](docs/add-a-tool.md) shows the data.
- **A new tool kind.** Also bring one built-in tool that uses it.

gateseal does not install tools, run hooks, open pull requests or administer
CI platforms, as the [vision's principles](VISION.md#principles) explain.
A change that adds one of those is declined.

For anything bigger than a fix, open an issue first, so the design is agreed
before the code is written.

## Set up

You need Git 2.54.0, uv 0.13.6 and mise 2026.10.19 or later. From the
repository root, create the development environment:

```sh
uv sync --locked --python 3.14.10
```

## Run the checks

```sh
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest tests -m 'not integration and not network'
uv run pytest tests -n auto -m 'not network'
```

The first pytest command runs the unit layer in seconds. The second adds the
integration layer, which builds a wheel, installs it and drives the installed
`gateseal` command in real Git repositories. Tests marked `network` read
private test fixtures and run in CI.
[Build and verify](docs/verification.md#build-and-test) describes each layer.

Run these too when they apply:

- **A change to `src/gateseal`.** Run the
  [mutation check](docs/verification.md#fix-a-failing-mutation-check) on the
  lines it changes:

  ```sh
  uv run pytestmut run --diff "$(git merge-base origin/main HEAD)"
  ```

- **A change to how activation prepares an attempt, how a commit picks one, or
  the interpreter store.** Run the
  [model check](docs/verification.md#model-check-the-prepared-attempts):

  ```sh
  uv run python formal/check.py
  ```

- **A change to the documentation.** Preview the site at
  `http://127.0.0.1:8000`:

  ```sh
  uv run --group docs mkdocs serve
  ```

## Open a pull request

- Keep to one change.
- Add or update the tests that show it.
- Update the documentation a user reads, and add a line under Unreleased in
  [CHANGELOG.md](CHANGELOG.md) when a user can notice the change.
- When the change settles a design question, add a
  [design record](docs/design/README.md).

CI runs the same checks on Linux and macOS, mutation testing on the changed
lines, the model check when its inputs change, and a strict build of the
documentation.

## Release a version

Release gateseal from `main` in three steps:

1. In one pull request, set `version` in `pyproject.toml` and `__version__` in
   `src/gateseal/__init__.py` to the new version, and run `uv lock`. In
   [CHANGELOG.md](CHANGELOG.md), move the Unreleased entries to a new
   `## [X.Y.Z] - YYYY-MM-DD` section.
2. Merge the pull request. This starts the Release workflow. The workflow
   waits until the Tests and Integration tests workflows pass on the merge
   commit. Then it builds the wheel and the sdist twice and requires the same
   SHA-256 for each file. It publishes both files to TestPyPI, installs the
   wheel that TestPyPI serves and runs the [demo](docs/demo.md) with it.
3. Approve the `pypi` environment. The workflow publishes both files to PyPI
   with attestations. Then it creates the `v<version>` GitHub release with
   both files and a `SHA256SUMS` file.

A push that keeps the version publishes nothing. To release the version that
`main` already has, such as the first release, start the workflow by hand on
`main`.

If a job fails, re-run the failed jobs of that run. If the job "Wait for the
tests" stopped on a cancelled run of Tests or Integration tests, re-run that run
first. Once PyPI holds the files, recover only this way, or run the workflow's
`gh release create` command by hand with the files of the run's `dist` artifact.

A new commit changes the sdist, and TestPyPI and PyPI refuse a changed file
under a name they already hold. So if the fix needs a new commit, merge it and
start the workflow by hand only while TestPyPI has no files for the version.
Otherwise release the next patch version.

## Report a security issue

Follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
