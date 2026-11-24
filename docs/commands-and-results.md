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
| `setup` | Install pre-commit and the launcher. `--refresh` adds the commit launcher to an existing clone, and the push launcher if a selected hook runs at a push. With the push launcher, it adds `hook.gateseal-lfs-upload` to the clone's Git configuration, which starts Git LFS at each push. It removes both when no worktree of the clone has a selected hook at a push, but keeps the upload hook while another push hook keeps Git LFS from writing its own. When it removes both in a clone that holds Git LFS objects, it runs `git lfs update`, so that Git LFS writes its own push hook. |
| `configure` | Generate the Lock and files from the Manifest. |
| `check` | Validate the selected policy and generated files. |
| `status` | Report readiness without network access, and warn about settings that skip the hooks. This is the default command. |
| `run` | Run commit checks on all tracked files using setup's pre-commit. |
| `fetch` | Download each selected release binary and build the hook environments. |
| `gate` | Run CI-stage checks and selected history scans. |
| `upgrade` | Select another policy, reviewed tool version, or gateseal release. |
| `policy init`, `lock`, `check`, `add`, `pin` | Create and maintain a policy directory. |

Repository commands reject an alternate `GIT_INDEX_FILE` with `alternate_index`.

## Status

`status` reports checks named `lock`, `pre-commit`, `launcher`, `push-launcher`, and `mise`. A check is `passed`, `failed`, or `skipped`. `status` exits 3 when a check fails, and 0 when none fails. `mise` is `skipped` unless a selected tool is a release binary or runs on the policy's Node. `push-launcher` is `skipped` unless a selected hook runs at a push. Its message then names a push launcher of setup that runs no check. `status` warns when `core.hooksPath` is set or mise's install at first use is off, and names the fix. It uses no network, so in a clone that has not stored its policy, the `lock` check fails and names `gateseal check`, which fetches it. `status` does not require setup to execute. It can report that setup is missing.

`status` and `check` describe each selected tool in a `tool` diagnostic. It names the version, capability, origin and kind, and the Node of a tool from npm, such as `Tool eslint 10.15.0 for typescript: built-in, npm-package on Node 24.22.3.`

## JSON results

The result has `schema`, `command`, `status`, `code`, `changed`, `diagnostics`, and `next_steps`. The `command` is the command that ran or failed, such as `policy lock`, or `null` when gateseal cannot tell it from the arguments, such as for an unknown command. The `status` is `ok`, `changed`, or `failed`. A `failed` result always has at least one next step: a command to run or a plain fix. Diagnostics can identify policies, tools, choices, repository hooks, reports, version exceptions, hooks, findings, or history scans.

A `check` diagnostic names the check and its status. A `hook` diagnostic names the hook, tool, origin, stage, status, and whether it modified files. Its status is `passed`, `failed`, `skipped`, or `stopped`. A hook is `stopped` when its tool lists the exit codes of its findings and the hook failed with another exit code, such as an audit that cannot reach its service. A `stopped` hook also gives its `exit_code`. For a tool from npm, the diagnostic and its findings also give the `node` version that the hook runs on. The hook `gateseal-node-packages`, which installs a Node project's packages before the hooks that read them, has the tool `gateseal` and the origin `built-in`. A `finding` can name a file, line, column, rule, and message. A history finding can also name a commit. A `history` diagnostic names the scan status and range. By default, a result lists at most 200 entries, including a gate's `duration` entry. A `truncated` diagnostic reports omitted entries; `--verbose` includes them. In GitHub Actions and Azure Pipelines, every result shows all its output and entries, as with `--verbose`.

A `download` diagnostic of `fetch` names what one download installed, and gives its `attempts`. `fetch` and `gate` try a download three times. A download that still fails ends the command with `download_failed`. The result then has the output of mise or pre-commit as a second diagnostic.

A `duration` diagnostic gives the `gate`'s time in `seconds`, to one decimal, before its other entries. Its message also names the slowest check when a check ran. It also gives `budget_seconds` when the policy sets a [time budget](ci-gate.md#time-budget). A gate over the budget also gives an `over_time_budget` warning. A `nothing_to_check` result has no time.

A warning leaves the exit code unchanged. Text output prints it as a `Warning:` line. In GitHub Actions, where `GITHUB_ACTIONS` is `true`, text output prints it as `::warning title=gateseal::<message>`. In Azure Pipelines, where `TF_BUILD` is `True`, text output prints it as `##vso[task.logissue type=warning]<message>`. Each provider shows that line as a warning on the run. `--json` output never holds this line.

## Exit codes

| Exit | Meaning |
| --- | --- |
| 0 | The operation succeeded. |
| 1 | A check failed or modified files. |
| 2 | Invalid invocation, repository, configuration, ownership, or tracked content. |
| 3 | The machine, clone, or stored policy is not ready. |
| 4 | A prerequisite, download, file operation, or executable failed, or the command was interrupted. |

The diagnostic names the result and any suggested action. `fetch` and `gate` report `download_failed` and exit 4 when a download fails, and `unsupported_machine` and exit 3 when a selected release binary has no archive for the machine. In both cases no check ran. `run` and `gate` report `tool_failed` when pre-commit exits with a code other than 0 or 1, or when a hook stopped. For a stopped hook, the message names the hook and its exit code. `gate` reports `missing_report` and exits 2 when a hook ran without a [report](tool-definitions.md#reports) that it reads, and the hook that writes it did not fail. `configure` and the policy commands report `missing_report` when no earlier hook writes a report that a hook reads.

During a commit, pre-commit streams hook output to Git. `gateseal-check` runs the version pinned by the staged Lock. When it stops a commit, it prints a `gateseal:` line and a `Next:` line, and pre-commit runs no later hook. gateseal redacts recognized credentials from captured child output. Other tool output can pass through unchanged.
