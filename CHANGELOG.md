# Changelog

All notable changes to gateseal are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0, a minor release may
change the manifest, policy or lock format, and its entry says how to move.

## [Unreleased]

### Added

- `gateseal setup` prepares a machine once: it installs pre-commit in a folder
  of its own and names a Git template with pre-commit's commit launcher in the
  global `init.templateDir`, so every new clone runs pre-commit at
  `git commit`. `gateseal setup --refresh` adds the launcher to an existing
  clone.

### Changed

- A commit runs pre-commit, not gateseal. pre-commit builds the environment of
  each hook repository, and mise installs each release binary at its first use
  and checks its SHA-256. A hook that starts a release binary names its mise
  request in `.pre-commit-config.yaml`, so `.gateseal/mise.toml` is no longer
  written.
- `gateseal run` and `gateseal gate` start the pre-commit that `gateseal setup`
  installed, with the caller's environment. Without it they stop with
  `needs_setup`.
- `gateseal status` and `gateseal doctor` report the same four checks: the
  Lock, the pre-commit from setup, the commit launcher and mise. `status` uses
  no network and exits 0. `doctor` exits 3 when a check fails, and warns when
  `core.hooksPath` is set or mise's install at first use is off.
- The generated GitHub workflow runs `gateseal setup`, `gateseal check` and
  `gateseal gate`.
- A Tool Definition's `stages` is `["commit", "run", "ci"]` or `["ci"]`.
- gateseal runs on Python 3.11 and later, the floor of the pre-commit version
  it names. It needed Python 3.14.10 or a later 3.14 release before.
- `hygiene` is optional. A policy defines at least one capability of any name,
  and a Manifest selects at least one of them, such as `secrets` alone.

### Removed

- `gateseal activate` and `gateseal upgrade --activate`. A clone needs no
  preparation after `gateseal setup`.
- Hook Python, the `[components.python]` table of a policy. A Python hook runs
  on the Python that `gateseal setup` installed pre-commit with.

## [0.1.0] - 2026-11-11

The first release.

### Added

- `gateseal configure` writes the manifest, the lock and native configuration
  for the hygiene, Python and secrets capabilities, and with `--ci github` a
  GitHub Actions workflow.
- The lock pins the compiler wheel by its file URL on PyPI, a raw GitHub URL at
  a full commit, or a local path, with its SHA-256.
- `gateseal activate` prepares the pinned compiler, Python, pre-commit and tools
  for one worktree, and every later `git commit` runs only what it prepared.
- `gateseal run`, `gateseal gate`, `gateseal check`, `gateseal status` and
  `gateseal doctor` run the checks, verify the repository against its lock and
  name the next step, each with a `--json` result.
- `gateseal upgrade` moves a policy, a tool or the compiler to a reviewed
  target, or back, and records a Version Exception with a reason and a
  review-by date.
- `gateseal policy init`, `add`, `pin`, `lock` and `check` start and maintain a
  team policy in Git, reading commits and checksums from upstream releases.
- A bundled reference policy with file hygiene, Ruff and Betterleaks.
- One sealed store per machine that every repository shares for its Python
  interpreters and pre-commit hook environments.

[Unreleased]: https://github.com/karstenevers/gateseal/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/karstenevers/gateseal/releases/tag/v0.1.0
