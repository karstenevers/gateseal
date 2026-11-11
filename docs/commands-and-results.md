# Commands and results

Run `gateseal` in a worktree to see its status. Use `--repo <path>` to
select a different worktree. For an operation's flags, run
`gateseal <command> --help`. Add `--json` for a machine-readable result or
`--verbose` for complete redacted diagnostics.

## Commands

| Command | Use it to |
| --- | --- |
| `configure` | Compile the Manifest into the Lock and generated files. See [Configure a repository](configure-a-repository.md). |
| `activate` | Prepare the locked tools for this worktree and install the Git hook. |
| `check` | Validate the tracked configuration without activation. For a missing Git policy, fetch the locked commit and verify its digest. |
| `status` | Inspect the local preparation without starting uv or mise or probing interpreters. This is the default command. |
| `doctor` | Check prepared file contents by hash, run the native tool probes, and report the prepared compiler and pre-commit versions. |
| `run` | Run checks on the working copies of all tracked files. |
| `gate` | Run CI-stage checks and history scans for an event. See [GitHub CI](github-ci.md#the-gate). |
| `upgrade` | Change the selected policy, component, or compiler. See [Upgrade and roll back](upgrade.md). |
| `policy` | Create or maintain a policy directory. See [Policy commands](#policy-commands). |

Repository commands use the worktree's own index. If `GIT_INDEX_FILE` selects
a different index, they report `alternate_index` and exit 2. A Git commit
checks [the index Git chose for that commit](how-a-commit-runs.md).

### Reading status

A successful `status` observation exits 0 even when the repository needs
attention. For example, `needs_activation` means the stored preparation or
policy is missing or damaged. `custom_hooks_path`, `unmanaged_hook`, and
`configured_hooks` identify hook settings that would prevent activation.
Read `code` and `next_steps` when deciding what to do.

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
| `code` | Specific outcome, such as `configured` or `needs_activation`. |
| `changed` | List of changes the operation reports. |
| `diagnostics` | Entries with `severity`, `category`, and `message`. Some categories add fields. |
| `next_steps` | Suggested actions, in order. |

### Policy diagnostics

`configure`, `check`, `status`, and `doctor` report the selected policy when
they can read it.

| Category | Information |
| --- | --- |
| `policy`, `policy_tag` | Release, source, digest, and the tag check at adoption for a Git policy. |
| `runtime`, `hook_python` | Compiler CPython and pre-commit versions, and the interpreter used by Python-language hooks. |
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
| 3 | Required local preparation is missing, stale, or damaged. Follow the reported next step. |
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
Git receives pre-commit's exit status. If preparation or staged validation
fails first, the error goes to standard error with a next step.
