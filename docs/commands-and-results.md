# Commands and results

Run `gateseal <command> --help` for the flags of a command. Repository commands accept `--repo <path>` to select a worktree. Many commands also accept `--json` and `--verbose`.

For example, in a configured repository before `gateseal setup`, run:

```sh
gateseal run --json
```

The command exits 3 with code `needs_setup`. Its `next_steps` contains `gateseal setup`.

The Manifest is `gateseal.toml`, which selects the policy and checks. The Lock is `gateseal.lock`, which records the selection and generated files.

## Commands

| Command | Purpose |
| --- | --- |
| `setup` | Install pre-commit and the launcher. `--refresh` adds the commit launcher to an existing clone, and the push launcher if a selected hook runs at a push. It removes a push launcher that it wrote when no worktree of the clone has a selected hook at a push. |
| `configure` | Generate the Lock and files from the Manifest. |
| `check` | Validate the selected policy and generated files. |
| `status` | Report readiness without network access. This is the default command. |
| `doctor` | Report readiness, fetch a missing policy, and warn about conflicting settings. |
| `run` | Run commit checks on all tracked files using setup's pre-commit. |
| `gate` | Run CI-stage checks and selected history scans. |
| `upgrade` | Select another policy, reviewed tool version, or gateseal release. |
| `policy init`, `lock`, `check`, `add`, `pin` | Create and maintain a policy directory. |

Repository commands reject an alternate `GIT_INDEX_FILE` with `alternate_index`.

## Status and doctor

Both commands report checks named `lock`, `pre-commit`, `launcher`, `push-launcher`, and `mise`. A check is `passed`, `failed`, or `skipped`. `push-launcher` is `skipped` unless a selected hook runs at a push. Its message then names a push launcher of setup that runs no check. `status` exits 0 even when a check fails, so read its result code and diagnostics. `doctor` exits 3 if a check fails. `doctor` can also warn about `core.hooksPath` or disabled mise auto-install. Neither command requires setup to execute. Both can report that it is missing.

## JSON results

The result has `schema`, `command`, `status`, `code`, `changed`, `diagnostics`, and `next_steps`. The `status` is `ok`, `changed`, or `failed`. A `failed` result always has at least one next step: a command to run or a plain fix. Diagnostics can identify policies, tools, choices, repository hooks, version exceptions, hooks, findings, or history scans.

A `check` diagnostic names the check and its status. A `hook` diagnostic names the hook, tool, origin, stage, status, and whether it modified files. A `finding` can name a file, line, column, rule, and message. A history finding can also name a commit. A `history` diagnostic names the scan status and range. By default, a result lists at most 200 entries, including a gate's `duration` entry. A `truncated` diagnostic reports omitted entries; `--verbose` includes them.

A `duration` diagnostic gives the `gate`'s time in `seconds`, to one decimal, before its other entries. It also gives `budget_seconds` when the policy sets a [time budget](ci-gate.md#time-budget). A gate over the budget also gives an `over_time_budget` warning. A `nothing_to_check` result has no time.

A warning leaves the exit code unchanged. Text output prints it as a `Warning:` line. In GitHub Actions, where `GITHUB_ACTIONS` is `true`, text output prints it as `::warning title=gateseal::<message>`. In Azure Pipelines, where `TF_BUILD` is `True`, text output prints it as `##vso[task.logissue type=warning]<message>`. Each provider shows that line as a warning on the run. `--json` output never holds this line.

## Exit codes

| Exit | Meaning |
| --- | --- |
| 0 | The operation succeeded. Read the `status` result code for readiness. |
| 1 | A check failed or modified files. |
| 2 | Invalid invocation, repository, configuration, ownership, or tracked content. |
| 3 | The machine, clone, or stored policy is not ready. |
| 4 | A prerequisite, download, file operation, or executable failed, or the command was interrupted. |

The diagnostic names the result and any suggested action. `run` reports `tool_failed` when pre-commit exits with a code other than 0 or 1.

During a commit, pre-commit streams hook output to Git. `gateseal-check` runs the version pinned by the staged Lock. When it stops a commit, it prints a `gateseal:` line and a `Next:` line. gateseal redacts recognized credentials from captured child output. Other tool output can pass through unchanged.
