# Changelog

All notable changes to gateseal are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/). Before 1.0, a minor release may
change the manifest, policy or lock format, and its entry says how to move.

## [Unreleased]

### Added

- `gateseal fetch` downloads each selected release binary with mise and
  builds the hook environments with pre-commit, before any hook runs. It tries
  a download three times. A download that still fails ends with
  `download_failed` and exit 4. The message names the archive or the hook
  repository and says that the code was not checked, and the result shows the
  output of mise or pre-commit.
- New keys in `[ci]` adjust the generated CI file. `branches` adds branches
  whose pushes the gate checks, such as `"release/*"`. `schedule = true` adds
  the weekly run. `timeout_minutes` sets the job's time limit. `pool` (Azure
  Pipelines) and `runs_on` (GitHub) run the job on your own agents or
  runners. `standalone = false` writes only the job, so that your own
  pipeline or workflow includes it and decides when it runs.
- The first step of the CI job stops with a clear message on a machine that
  is not Linux on x86-64 with glibc.
- The `uv-project` tool kind. A tool of this kind runs in the repository's own
  uv project, so a type checker reads the packages that the project installs.
  Its hook starts the program with `uv run --locked --all-packages --with
  <package>==<version>`: the policy pins the tool, and the repository's
  `uv.lock` pins what the tool reads. `gateseal configure` and `gateseal
  check` stop with `missing_project` in a repository without `pyproject.toml`
  or `uv.lock`, or with `tool.uv.managed = false`.
- The `project` scope of a tool definition. Its hooks get no file names and
  check the whole project. A hook runs at every commit, or, when it lists
  `types_or`, only when a file of those types changed.
- Reports. A hook of a tool definition lists in `writes` each file that it
  leaves for a later hook, and in `reads` each one that it takes. A report is
  `.gateseal/reports/<name>`, and an argument names it as `{<name>}`. A policy
  whose hook reads a report that no earlier hook writes stops with
  `missing_report`, and so does a Manifest that selects the reading hook
  without the writing one. A selection with a writing hook owns
  `.gateseal/reports/.gitignore`, so Git ignores the reports. `gateseal gate`
  deletes the reports of an earlier run first, and exits 2 with
  `missing_report` when a hook ran without a report that it reads, unless
  the writing hook failed. `check`,
  `status` and `doctor` name each report and the hook that writes it.
- The bundled policy's `python-types` capability, which runs Pyright in strict
  mode as a built-in `uv-project` tool. A repository that selects `python`
  alone gets no new check.
- Python test checks at a push and in the CI gate, as three opt-in
  capabilities of the bundled policy. `python-tests` runs the tests once under
  coverage.py 7.17.3 and writes coverage.py's JSON report. `python-crap` reads
  that report and fails a function in a changed Python file whose CRAP score
  is over 10, unless `.crap-baseline.toml` lists it. A change may lower or
  remove a baseline entry, but not add or raise one. `python-mutation` runs
  pytestmut 0.1.0 on the lines that a push or a ranged gate changes, and fails
  on any mutant that no test kills. Each starts only when a Python file
  changed. A repository that selects none of them gets no new check.
- Python dependency checks, as three opt-in capabilities of the bundled
  policy. `python-lock` runs `uv lock --check` with uv 0.13.9 at each commit,
  in `gateseal run` and in the CI gate, and a stale `uv.lock` stops the commit
  with a message that names `uv lock`. `python-dependencies` runs deptry
  0.25.1 in the uv project and names each unused, missing or transitive
  dependency. `python-audit` runs `uv audit` with uv 0.13.9 in the CI gate
  only. It asks `api.osv.dev` about each locked package, so a new advisory can
  fail a weekly run without a commit. The policy's `rules/uv-audit.toml` lists
  the ignored advisories, and a repository cannot add one. A repository that
  selects none of them gets no new check.
- `finding_exit_codes` in a hook of a tool definition: the exit codes with
  which the tool reports a finding. A hook that fails with another exit code
  is `stopped`, and `gateseal run` and `gateseal gate` report `tool_failed`
  and exit 4. An audit that cannot reach its service fails the gate this way.
- The push stage. A tool definition or a repository hook with
  `stages = ["push", "ci"]` runs at `git push` and in the CI gate, and not at
  commit or in `gateseal run`. A repository hook takes the new `stages` field.
  The generated `.pre-commit-config.yaml` of such a selection lists `pre-push`
  in `default_install_hook_types`, so `pre-commit install` and
  `gateseal setup --refresh` write a push launcher beside the commit launcher.
  `status` and `doctor` report it as the `push-launcher` check. A selection
  without a push hook compiles to the same files as before, and its clones
  get no push launcher. `gateseal setup --refresh` removes a push launcher
  that it wrote when no worktree of the clone has a push hook any more.
- The `gateseal-lfs-check` hook, which runs at each push of a selection with
  a push hook. pre-commit's push launcher takes the place of the push hook of
  Git LFS, and runs that hook only if it was there first. The check stops a
  push from a clone that has no push hook that starts Git LFS, when a file of
  its index or a commit that the remote lacks is of Git LFS. It names the
  repair: `git lfs update --force`, then `pre-commit install`, or a line to
  add to a push hook that pre-commit kept. It accepts a push hook of Git's
  configuration that runs `git lfs pre-push`.
- A failed gate in GitHub Actions or Azure Pipelines shows more. The job log
  shows all the hook output, in color, and the diff of each file that a hook
  changed. Each failed hook adds an error annotation, on the file and line
  when the hook reports them. The run gets a summary of the failed checks and
  the next steps. In CI, every result shows all its output, as with
  `--verbose`.

- The CI gate lists the time of each check in its log, and names the
  slowest. The warning of a gate over its time budget names the slowest
  check and its time too.

### Changed

- The generated CI file downloads the selected tools in a step of its own,
  before the gate. The next `gateseal upgrade` writes the file with the step.
  `gateseal gate` makes the same downloads before its first hook, so a failed
  download ends the gate with `download_failed` and no longer with
  `checks_failed`. The gate's time leaves out these downloads.
- The weekly run of the generated CI file is now off by default. The next
  `gateseal configure` or `gateseal upgrade` writes the file without it. Add
  `schedule = true` to `[ci]` to keep it.
- The CI job keeps its home folder and everything it installs in the job's
  temporary folder. It no longer changes the global Git configuration or the
  home folder of the agent's user.
- The bundled policy offers hooks with the IDs `pyright`, `uv-lock`,
  `uv-audit` and `deptry`, and the names `pyright`, `uv lock check`,
  `uv audit` and `deptry`. A Manifest with a repository hook of such an ID or
  name stops with `duplicate_identifier`, also when it does not select the
  hook's capability. Rename the repository hook.
- Two tools of a policy may take hooks from the same hook repository. Their
  hook IDs, hook names and outputs stay distinct.
- The bundled policy pins Ruff 0.17.5 and Betterleaks 1.10.1. It still reviews
  Ruff 0.17.1 and Betterleaks 1.9.0 and 1.8.0, so a Version Exception can keep
  one of them. The policy keeps its label `2026.11-team-tools`, so
  `gateseal upgrade` to this release stops with `bundled_policy_moves` until
  you add `--policy bundled`.

### Security

- gateseal redacts a recognized credential in captured output even when
  terminal color codes split it or come right before it.
- In GitHub Actions and Azure Pipelines, a workflow or logging command in
  captured output, such as a hook's output, no longer runs.

## [0.2.0] - 2026-11-18

This release keeps no compatibility with 0.1.0. Every command refuses a Lock
that 0.1.0 wrote, and `gateseal activate` is gone. To move a repository from
0.1.0, delete `gateseal.lock`, `.pre-commit-config.yaml`, `.gateseal/` and
`.github/workflows/gateseal.yml`, then run `gateseal configure`.

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
- Azure Pipelines as a second CI provider. `gateseal configure --ci azure`
  writes `azure-pipelines-gateseal.yml`, which the Lock owns. Its builds run
  the CI gate on a Microsoft-hosted `ubuntu-24.04` agent for pull request
  validation, pushes to the default branch, a weekly schedule and manual
  runs. A Git policy on an `https://dev.azure.com/` URL gets the job's token,
  or the secret variable `GATESEAL_POLICY_TOKEN`, for that repository only.
  The pipeline names that repository as a resource that its job uses, which
  the job's token needs to read it.
  With `TF_BUILD=True`, text output prints each warning as a
  `##vso[task.logissue type=warning]` logging command.
- `gateseal gate --merge COMMIT` checks what a pull request's merge commit
  adds to its first parent, for a CI provider that names no base commit.
  `gateseal gate --after COMMIT` without `--before` checks every file and
  scans every commit reachable from that commit, for a push whose event does
  not name the commit it replaced.

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
  `unsupported_schema`.
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

[Unreleased]: https://github.com/karstenevers/gateseal/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/karstenevers/gateseal/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/karstenevers/gateseal/releases/tag/v0.1.0
