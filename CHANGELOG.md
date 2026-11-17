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
- The `gateseal-check` hook. `.pre-commit-config.yaml` lists gateseal's
  repository first, at the commit that the Lock pins, so pre-commit builds
  that gateseal and runs its hook before the tools. The hook compares the
  staged `gateseal.toml` and each staged generated file with the SHA-256 that
  the staged Lock records, with no network and no policy. A commit that stages
  an edited generated file stops and names `gateseal configure`. `gateseal
  run` skips the hook.
- `check_at_commit` in a tool definition's `[executable]`. `gateseal policy
  lock` records the SHA-256 of the program in each archive of such a tool,
  and the `gateseal-check` hook compares the program that mise starts with
  it, because mise never checks an installed tool again. Betterleaks sets it,
  so a commit on a machine with a replaced Betterleaks stops and names it. A
  team policy that reviews Betterleaks stops loading until you run `gateseal
  policy lock` on its directory again, which records these SHA-256s, and
  release it.
- Repository hooks. A `[hooks.<id>]` table in `gateseal.toml` runs a script
  of the repository after every policy hook, at a commit, in `gateseal run`
  and in the CI gate. It only adds a check: an ID or a name that a policy hook
  uses is refused.
- Optional hooks. `optional` in a capability of `policy.toml` lists hooks that
  a repository may leave out with `skip` in `[choices.<capability>]`. A
  skipped hook leaves the generated configuration, so a commit and the CI
  gate both skip it. gateseal reserves `skip` in every capability, as
  `exclude`.
- A page on how to adjust a setup, which lists each way to change the checks
  and who makes the change, and a section on hooks of your own in Work in a
  configured repository.
- A time budget for the CI gate. `time_budget_seconds` in `policy.toml` sets
  it in whole seconds. Each `gateseal gate` that checks files reports its time
  in a `duration` diagnostic. A gate over the budget also gives an
  `over_time_budget` warning and keeps the exit code that its checks decide.

### Changed

- The bundled `secrets` rules start from the rules built into Betterleaks. A
  commit now also stops on an AWS secret access key, a private key, a
  password, and tokens of services such as Slack, Stripe and npm. gateseal's
  own rules for an AWS access key ID alone and for the `ghp_`, `gho_`, `ghu_`,
  `ghs_` and `ghr_` GitHub tokens stay, and Betterleaks' built-in rule finds
  fine-grained `github_pat_` tokens. A new rule finds a base64 key that
  starts with `+` or `/`, which the built-in generic rule misses. The
  placeholder filter no longer drops a secret that starts with `true`, holds
  `false` or ends with `null`. A policy's Betterleaks or Gitleaks rule file
  can use an `extend` table with `useDefault` and `disabledRules`. Any other
  `extend` is still refused.
- Every failed result names at least one next step: a command to run or a
  plain fix. In `--json`, `next_steps` is never empty when `status` is
  `failed`, and text output and the `gateseal-check` hook print a `Next:`
  line.
- With `GITHUB_ACTIONS=true`, text output prints each warning as a
  `::warning` workflow command instead of a `Warning:` line, which GitHub
  shows as an annotation. This also applies to the overdue and redundant
  Version Exception warnings of `gateseal check`.
- A commit runs pre-commit, not the installed gateseal command. pre-commit
  builds the environment of each hook repository, and mise installs each
  release binary at its first use and checks its SHA-256. A hook that starts a
  release binary names its mise request in `.pre-commit-config.yaml`, so
  `.gateseal/mise.toml` is no longer written.
- `gateseal run` and `gateseal gate` start the pre-commit that `gateseal setup`
  installed, with the caller's environment. Without it they stop with
  `needs_setup`.
- `gateseal status` and `gateseal doctor` report the same four checks: the
  Lock, the pre-commit from setup, the commit launcher and mise. `status` uses
  no network and exits 0. `doctor` exits 3 when a check fails, and warns when
  `core.hooksPath` is set or mise's install at first use is off.
- The generated GitHub workflow runs `gateseal setup`, `gateseal check` and
  `gateseal gate`.
- A new push to a pull request cancels the older run of the generated GitHub
  workflow for that pull request. Runs for pushes, merge groups, manual starts
  and the schedule never cancel each other. A repository gets the new workflow
  when it moves to this release.
- A Tool Definition's `stages` is `["commit", "run", "ci"]` or `["ci"]`.
- gateseal runs on Python 3.11 and later, the floor of the pre-commit version
  it names. It needed Python 3.14.10 or a later 3.14 release before.
- `hygiene` is optional. A policy defines at least one capability of any name,
  and a Manifest selects at least one of them, such as `secrets` alone.
- `policy.toml` writes each tool once, as a table under its capability:
  `[capabilities.<capability>.tools.<tool>]` holds its hooks, rule file, `pin`,
  `reviewed` versions and `requires`, and `[capabilities.<capability>.choices]`
  gives each exposed choice its default. This is policy schema 5, and gateseal
  refuses a policy of an older schema with `unsupported_schema`.
- Any capability can expose `exclude`, except one that holds a tool with a
  history scan, so that a commit and the CI gate check the same files. The
  bundled policy still exposes it only for `hygiene`.
- The Lock pins gateseal by its repository, its version and the commit of its
  release tag, which `gateseal configure` reads with the user's Git. The
  generated workflow installs gateseal from that commit. This is Lock format 2,
  and every command refuses a Lock of another format with
  `unsupported_schema`. To move a repository from 0.1.0, delete
  `gateseal.lock`, `.pre-commit-config.yaml`, `.gateseal/` and
  `.github/workflows/gateseal.yml`, then run `gateseal configure`.
- `gateseal upgrade` run by another gateseal release moves the Lock to that
  release, newer or older. It reads only the old Lock's ownership ledger: its
  format, its gateseal pin, the Manifest's SHA-256, the policy's digest and
  the files it lists. A listed file that the release never writes stops the
  move, and so does a bundled policy with other content under the same label.
- When `gateseal.lock` pins another gateseal release, the next step of
  `selected_compiler_required` runs that release with `uvx gateseal@VERSION`
  instead of asking you to install it. After a move to another release,
  `gateseal upgrade` asks each developer to run `uvx gateseal@VERSION setup`.
- The Lock no longer records the CPython and pre-commit that gateseal runs.
- A hook repository record in the Lock no longer has `tools`.

### Removed

- `gateseal activate` and `gateseal upgrade --activate`. A clone needs no
  preparation after `gateseal setup`.
- Hook Python, the `[components.python]` table of a policy. A Python hook runs
  on the Python that `gateseal setup` installed pre-commit with.
- `--compiler-wheel`, `--compiler-source` and `--compiler-sha256` of
  `gateseal configure` and `gateseal upgrade`, and the candidate compiler that
  an upgrade installed to render a compiler move.
- The Tool Definition fields that only the removed preparation read:
  `removed_prefixes`, a hook's `probe`, and `version` and `version_output` in
  `[executable]`. A definition that still sets one stops with `invalid_field`.
  A pre-commit repository no longer takes an `[executable]` table.

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
