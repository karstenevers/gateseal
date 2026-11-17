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
| `stages` | `["commit", "run", "ci"]` for a tool that runs everywhere, or `["ci"]` for a tool that runs only in the [CI gate](github-ci.md#the-gate). |
| `scope` | `files`. pre-commit passes filenames to the hooks. |
| `source` | The [source fields for the tool's kind](#kinds). |
| `configuration.format` | Optional. Use `toml` if the policy supplies a configuration. Configure writes it to `.gateseal/<name>.toml`. |
| `executable` | Names the program that a hook starts. Required for a release binary, and refused for a pre-commit repository. |
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
| `output` | Use `concise` for findings in the form `file:line:column: rule message`. gateseal reports each as a JSON `finding`. Use `plain` to report only passed, failed, or modified. |

To pass an owned file, use `{configuration}` or `{<files key>}` as a whole
argument. For example, the argument `{report}` names the file defined in
`[files.report]`. Other braces are invalid. gateseal passes other literals,
such as `/dev/null`, directly to the tool without a shell.

## Executable

| Field | Accepted value or purpose |
| --- | --- |
| `name` | The bare program name. A release-binary hook starts it through mise. |
| `check_at_commit` | Optional, `false` by default. Set it to `true` for a release binary that a commit should check after mise installs it, such as a secret scanner. `gateseal policy lock` then records the SHA-256 of the program in each archive, and the `gateseal-check` hook compares the installed program with it at each commit. |

## Kinds

### `pre-commit-repository`

Set `source.repository` to the HTTPS repository URL and `source.tag` to its
release tag pattern. Use a repository URL without credentials, a query,
or a trailing slash. Include `{version}` in the pattern, for example
`v{version}`.

`gateseal policy lock` records the commit each reviewed tag points to.
pre-commit clones that commit and builds the hook environment at the hook's
first use. A Python hook runs on the Python that pre-commit runs on. With
setup's pre-commit, that is the Python that `gateseal setup` installed.

### `release-binary`

| `source` field | Accepted value or purpose |
| --- | --- |
| `repository`, `tag` | The HTTPS repository URL and the release tag pattern with `{version}`. |
| `archive` | The archive filename. Include `{platform}`. You can also include `{version}`. |
| `checksums` | The release's checksum filename. You can include `{version}`. |
| `platforms` | Map each platform the tool supports to its name in the release filenames. Include at least one of `linux-x64` and `macos-arm64`. |

`gateseal policy lock` downloads each new archive and verifies the published
SHA-256. It records the archive URL and checksum for each platform, and the
hook's entry in `.pre-commit-config.yaml` names them. pre-commit runs the
entry as a `local` hook, one start at a time. At the first use, mise
downloads the archive for the machine's platform, checks the checksum, and
installs it once for all repositories on the machine that lock the same
archives. The tool name in the entry ends in a digest of the archives, so a
policy with other archives for the same version gets its own install.

### Environment

A release binary starts through `mise --no-config exec --deny-env`. No mise
file changes which archive runs, and the program gets only the variables that
mise keeps, such as `HOME` and `PATH`. The gate's history scan also gets
`GIT_NO_LAZY_FETCH`. A hook of a pre-commit repository gets
the caller's environment, so a variable such as `RUFF_OUTPUT_FILE` reaches
Ruff.

mise does not check an installed tool again. For an executable with
`check_at_commit = true`, the `gateseal-check` hook asks mise which program
the hook's request starts and compares it with the recorded SHA-256. mise
starts the program in the install's `bin` folder when the install has one.
The hook skips a tool that mise has not installed yet. With its
install turned off by `MISE_AUTO_INSTALL` or `MISE_EXEC_AUTO_INSTALL`, mise
starts a program of the same name from `PATH`, and `gateseal doctor` warns
about it.

## Choices

A choice sets one top-level key in the tool's owned configuration. gateseal
writes the key before the rest of the policy's configuration. Ruff's
`target_version` is an example.

| Field | Accepted value or purpose |
| --- | --- |
| `key` | A distinct top-level configuration key matching `[a-z][a-z0-9-]*`. The reserved key `extend` could load another configuration. |
| `since` | Map each accepted value to the earliest tool release that supports it. Use lowercase letters and digits for values. |

To expose the choice, give it a default as `<name> = <default>` in
`[capabilities.<capability>.choices]` in `policy.toml`. A repository can select a different value that its tool
version supports.

## What a team definition guarantees

The tool kind enforces the exact pin: pre-commit clones the recorded commit,
and mise checks the recorded checksum before it installs a binary. Each hook
uses the owned configuration. A hook that cannot start stops the commit.

The compiler treats the definition as data and runs programs only through
the selected kind. Review the tool's own behavior and arguments separately.
For example, a scanner can still read a repository's ignore file unless
its arguments disable that behavior. See the
[Gitleaks example](add-a-tool.md#what-the-gitleaks-definition-guarantees).
