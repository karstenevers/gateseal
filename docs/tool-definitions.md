# Tool definition reference

A tool definition tells gateseal where to find a tool, how to verify it, and
which hooks to run. A policy can add a tool by putting its definition in
`tools/<name>.toml`. Built-in tools use the same [schema](policy-contract.md).
For complete Gitleaks and Black definitions, see [Add a tool](add-a-tool.md).

## Name and origin

The filename sets the tool name. It must match `[a-z][a-z0-9-]*`. List the
name under a capability in `policy.toml`.

| Name in the policy | How gateseal resolves it |
| --- | --- |
| `pre-commit-hooks`, `ruff`, `betterleaks` | Uses the built-in definition. To change its arguments, copy it under a new name. |
| A team-defined name | Reads `tools/<name>.toml`. A missing definition gives `unknown_tool`. |
| A team definition with a built-in name, `pre-commit`, or `python` | Fails with `reserved_tool_name`. The last two names belong to the compiler runtime. |

`status`, `doctor`, `check`, and `configure` identify selected tools as
`built-in` or `team-defined`. In `run --json` and `gate --json`, the
`origin` field gives the same label on each hook, finding, and history entry.

## Shared fields

| Field | Accepted value or purpose |
| --- | --- |
| `kind` | `pre-commit-repository` or `release-binary`. gateseal reads this field first. |
| `stages` | All three stages, `["commit", "run", "ci"]`. |
| `scope` | `files`. pre-commit passes filenames to the hooks. |
| `removed_prefixes` | Unique environment variable prefixes, such as `GITLEAKS_`. While the tool is selected, gateseal removes matching variables from each process it starts. A prefix must preserve `GIT_DIR` and `GIT_INDEX_FILE`, which identify the index being committed. |
| `source` | The [source fields for the tool's kind](#kinds). |
| `configuration.format` | Optional. Use `toml` if the policy supplies a configuration. Configure writes it to `.gateseal/<name>.toml`. |
| `executable` | Describes the prepared program and its version check. Required for a release binary. |
| `files.<key>` | Optional. A lowercase `extension` and text `content` with LF line endings. Configure writes the file to `.gateseal/<name>-<key>.<extension>`. |
| `hooks.<id>` | A [hook](#hooks). Define at least one. |
| `history.args` | Optional for a release binary. Arguments for a revision-range scan in the [CI gate](github-ci.md#the-gate). Include `{range}` exactly once. |
| `choices.<name>` | Optional. A [choice](#choices) that sets a value in the tool's configuration. Requires `configuration`. |

The compiler defines the allowed values for `kind`, `stages`, `scope`,
`output`, `configuration.format`, and platform keys. An unsupported value
gives `unsupported_tool_definition` before gateseal writes files. If a
policy needs a newer value, set `minimum_compiler` to the first compiler
that supports it. A field outside the selected kind's schema is an unknown
field.

## Hooks

| Field | Accepted value or purpose |
| --- | --- |
| `name` | The name pre-commit displays. Each hook ID and name must be unique across the policy. |
| `args` | Optional. Literal arguments to pass to the hook. |
| `types_or` | Optional. The pre-commit file types to match. |
| `probe` | A command for activation and `doctor` to run at the worktree root. Start with the bare name of a program the tool kind prepares. The probe receives no filenames and reads empty standard input. |
| `output` | Use `concise` for findings in the form `file:line:column: rule message`. gateseal reports each as a JSON `finding`. Use `plain` to report only passed, failed, or modified. |

To pass an owned file, use `{configuration}` or `{<files key>}` as a whole
argument. For example, the argument `{report}` names the file defined in
`[files.report]`. Other braces are invalid. gateseal passes other literals,
such as `/dev/null`, directly to the tool without a shell.

## Executable

| Field | Accepted value or purpose |
| --- | --- |
| `name` | The bare program name. A release-binary hook runs it through the prepared attempt's `tools/gateseal-<name>` link. Start each release-binary probe with this name. |
| `version` | The arguments that make the program print its version. |
| `version_output` | A regular expression for the entire output after trimming whitespace. Include `{version}` once. It matches the exact selected version, so `8.30.10` cannot satisfy a pin of `8.30.1`. |

Declare an executable for a pre-commit repository if it installs its tool
from a package index by version, as `ruff-pre-commit` does. For hooks
implemented in the repository itself, such as `pre-commit-hooks`, the commit
identifies the version.

## Kinds

### `pre-commit-repository`

Set `source.repository` to the HTTPS repository URL and `source.tag` to its
release tag pattern. Use a repository URL without credentials, a query,
or a trailing slash. Include `{version}` in the pattern, for example
`v{version}`.

`gateseal policy lock` records the commit each reviewed tag points to.
pre-commit clones that commit and builds the hook environment. It uses the
policy's [Hook Python](policy-format.md#hook-python) if set. Otherwise it
uses the compiler runtime's CPython. Activation rejects an environment built
with a different interpreter.

### `release-binary`

| `source` field | Accepted value or purpose |
| --- | --- |
| `repository`, `tag` | The HTTPS repository URL and the release tag pattern with `{version}`. |
| `archive` | The archive filename. Include `{platform}`. You can also include `{version}`. |
| `checksums` | The release's checksum filename. You can include `{version}`. |
| `platforms` | Map each platform the tool supports to its name in the release filenames. Include at least one of `linux-x64` and `macos-arm64`. |

`gateseal policy lock` downloads each new archive and verifies the published
SHA-256. It records the archive URL and checksum for each platform. During
activation, mise installs the verified binary inside the prepared attempt.
pre-commit runs it as a `local` hook.

## Choices

A choice sets one top-level key in the tool's owned configuration. gateseal
writes the key before the rest of the policy's configuration. Ruff's
`target_version` is an example.

| Field | Accepted value or purpose |
| --- | --- |
| `key` | A distinct top-level configuration key matching `[a-z][a-z0-9-]*`. The reserved key `extend` could load another configuration. |
| `since` | Map each accepted value to the earliest tool release that supports it. Use lowercase letters and digits for values. |

To expose the choice, set a default in `[choices.<capability>.<name>]` in
`policy.toml`. A repository can select a different value that its tool
version supports.

## What a team definition guarantees

The tool kind enforces the exact pin and verifies the recorded commit or
checksum. Activation and `doctor` check the prepared tool. gateseal runs
the prepared program, uses the owned configuration, and removes environment
variables with the declared prefixes. If preparation is missing or invalid,
the check stops and points to `gateseal activate`.

The compiler treats the definition as data and runs programs only through
the selected kind. Review the tool's own behavior and arguments separately.
For example, a scanner can still read a repository's ignore file unless
its arguments disable that behavior. See the
[Gitleaks example](add-a-tool.md#what-the-gitleaks-definition-guarantees).
