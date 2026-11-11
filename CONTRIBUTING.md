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

## Report a security issue

Follow [SECURITY.md](SECURITY.md) instead of opening an issue.

## License

By contributing, you agree that your contribution is licensed under the
[MIT License](LICENSE).
