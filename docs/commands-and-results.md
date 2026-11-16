# Commands and results

Run `gateseal` in a worktree to see its status. Use `--repo <path>` to
select a different worktree. For an operation's flags, run
`gateseal <command> --help`. Add `--json` for a machine-readable result or
`--verbose` for complete redacted diagnostics.

## Commands

| Command | Use it to |
| --- | --- |
| `setup` | Install pre-commit and its commit launcher for every new clone on this machine, once. With `--refresh`, also add the launcher to the clone at `--repo`. See [Set up pre-commit](install.md#set-up-pre-commit-on-this-machine). |
| `configure` | Compile the Manifest into the Lock and generated files. See [Configure a repository](configure-a-repository.md). |
| `check` | Validate the tracked configuration. For a missing Git policy, fetch the locked commit and verify its digest. |
| `status` | Report whether this clone is ready to commit, without the network. This is the default command. See [Reading status and doctor](#reading-status-and-doctor). |
| `doctor` | Run the checks of `status`, fetch a missing Git policy, and warn about settings that keep a hook from running. |
| `run` | Run the commit checks on the working copies of all tracked files, with the pre-commit that `setup` installed. |
| `gate` | Run CI-stage checks and history scans for an event. See [GitHub CI](github-ci.md#the-gate). |
| `upgrade` | Change the selected policy, component, or compiler. See [Upgrade and roll back](upgrade.md). |
| `policy` | Create or maintain a policy directory. See [Policy commands](#policy-commands). |

Repository commands use the worktree's own index. If `GIT_INDEX_FILE` selects
a different index, they report `alternate_index` and exit 2. A Git commit
checks [the index Git chose for that commit](how-a-commit-runs.md).

### Reading status and doctor

`status` and `doctor` report the same four checks. Each is a `check`
diagnostic with the check's name in `check` and a `status` of `passed`,
`failed`, or `skipped`.

| Check | Passes when | Next step when it fails |
| --- | --- | --- |
| `lock` | The Manifest, the Lock, and the generated files agree with the policy. | The step that the message names, such as `gateseal configure` or `gateseal check`. |
| `pre-commit` | The pre-commit that `gateseal setup` installed starts. | `gateseal setup` |
| `launcher` | The clone's `pre-commit` hook is the commit launcher from setup, and it is executable. | `gateseal setup --refresh` |
| `mise` | mise 2026.10.19 or later is on `PATH`. The check is skipped when no selected tool is a release binary, or when the Lock did not load. | Install mise. |

The result code is `healthy` when no check failed, and `unhealthy` otherwise.

- `status` uses no network and exits 0 in both cases, so read `code` and
  `next_steps`. In a repository without `gateseal.toml`, it reports
  `unconfigured`.
- `doctor` exits 3 when a check fails. It fetches a Git policy that the clone
  has not stored. It also warns with `custom_hooks_path` when Git's
  `core.hooksPath` is set, because `git commit` then skips the launcher, and
  with `mise_auto_install_off` when `MISE_AUTO_INSTALL` or
  `MISE_EXEC_AUTO_INSTALL` turns off mise's install of a release binary.

### Policy commands

Policy commands take a policy directory instead of `--repo`. In this syntax,
angle brackets mark required arguments and square brackets mark optional ones.

```text
gateseal policy init <directory>
gateseal policy lock [<directory>]
gateseal policy check [<directory>]
gateseal policy add <tool> <version> [<directory>]
gateseal policy pin <tool> <version> [<directory>]
```

`init` needs a new or empty directory. Otherwise it fails with
`directory_not_empty` and writes nothing. The other commands default to the
current directory. See [Policy Lock](policy-lock.md) for their result codes.

## JSON results

| Field | Meaning |
| --- | --- |
| `schema` | Result format version, currently `1`. |
| `command` | Operation name, such as `status` or `policy add`. |
| `status` | `ok`, `changed`, or `failed`. |
| `code` | Specific outcome, such as `configured` or `unhealthy`. |
| `changed` | List of changes the operation reports. |
| `diagnostics` | Entries with `severity`, `category`, and `message`. Some categories add fields. |
| `next_steps` | Suggested actions, in order. |

### Policy diagnostics

`configure`, `check`, `status`, and `doctor` report the selected policy when
they can read it.

| Category | Information |
| --- | --- |
| `policy`, `policy_tag` | Release, source, digest, and the tag check at adoption for a Git policy. |
| `gateseal` | The gateseal release, repository, and commit that the Lock pins, and the pre-commit version it runs. |
| `tool`, `policy_choice` | Each tool's version, capability, kind, and origin, and each selected choice. |
| `version_exception` | Component, selected version, policy pin, review date, and reason. |
| `overdue_exception`, `redundant_exception` | The local date is past the review date, or the selected version equals the pin. Review the exception in the Manifest. |

Warnings leave the exit code unchanged.

### Hooks and findings

`run` and `gate` report checks in the order pre-commit prints them. Entries
identify the `tool`, its `origin` (`built-in` or `team-defined`), and the
`stage` (`run` or `ci`).

| Category | What to read |
| --- | --- |
| `hook` | `hook` identifies the check. `status` is `passed`, `failed`, or `skipped`. `modified` shows whether it changed files. A skipped hook's message includes the reason. |
| `finding` | `hook`, `file`, `line`, `column`, `rule`, and `message`. For notebooks, `line` counts within `cell`. A history finding has `hook` set to `history` and includes `commit`. |
| `history` | The history scan's `status` and revision range. |

Ruff's lint hook, Betterleaks, and hooks that declare `output = "concise"`
produce structured findings. The default result includes at most 200 hook,
finding, and history entries. A `truncated` diagnostic gives the number
omitted. Use `--verbose` to see them all.

## Exit codes

| Exit | Meaning |
| --- | --- |
| 0 | The operation succeeded. For `status`, read the result code for readiness. |
| 1 | A native check failed or modified files. Review the findings or edits, stage corrections, and retry. |
| 2 | Invalid invocation, repository, configuration, ownership, or tracked content. The result identifies the conflict. |
| 3 | The machine or the clone is not ready: `gateseal setup` has not run, a `doctor` check failed, or a stored policy is missing or damaged. Follow the reported next step. |
| 4 | A download, file operation, prerequisite, or executable failed. Ctrl-C also returns 4 with `interrupted` during a command. |

When pre-commit exits with a code other than 0 or 1 during `run`, gateseal
reports `tool_failed` and directs you to `gateseal doctor`.

## Redaction

gateseal captures child-process output and redacts URL passwords, HTTPS
usernames, URL query values, authorization headers, and recognized GitHub
tokens. Other tool output can pass through unchanged. Betterleaks also
redacts its own findings.

## Commit output

During a commit, pre-commit streams output to Git's terminal. A finding
appears below its hook name, usually as `file:line:column: rule message`.
Git receives pre-commit's exit status. The installed gateseal command does
not run during a commit. The `gateseal-check` hook runs the gateseal that the
Lock pins. When it stops a commit, it prints a `gateseal:` line and a `Next:`
line.
