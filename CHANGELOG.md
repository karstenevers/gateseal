# Changelog

All notable changes to gateseal are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0, a minor release may
change the manifest, policy or lock format, and its entry says how to move.

## [Unreleased]

## [0.1.0] - Unreleased

The first release.

### Added

- `gateseal configure` writes the manifest, the lock and native configuration
  for the hygiene, Python and secrets capabilities, and with `--ci github` a
  GitHub Actions workflow.
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
