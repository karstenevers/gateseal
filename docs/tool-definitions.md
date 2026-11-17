# Tool definitions

A policy can describe a tool in `tools/<name>.toml`. The definition gives its source and hooks. Its filename sets the tool name, which must match `[a-z][a-z0-9-]*`. List that name under a capability in `policy.toml`.

For example, this source fragment describes the Black hook repository:

```toml
[source]
repository = "https://github.com/psf/black-pre-commit-mirror"
tag = "{version}"
```

See [Add a tool](add-a-tool.md) for complete definitions. Built-in definitions exist for `pre-commit-hooks`, `ruff`, and `betterleaks`. A team definition cannot reuse those names or the reserved names `pre-commit` and `python`.

## Shared fields

| Field | Value |
| --- | --- |
| `kind` | `pre-commit-repository` or `release-binary`. |
| `stages` | `["commit", "run", "ci"]` or `["ci"]`. |
| `scope` | `files`. pre-commit passes matching filenames. |
| `configuration.format` | Optional `toml`. The generated file is `.gateseal/<name>.toml`. |
| `files.<key>` | Optional fixed file with lowercase `extension` and text `content` using LF line endings. |
| `hooks.<id>` | At least one hook. |
| `history.args` | Optional release-binary arguments for a CI history scan. Include `{range}` exactly once. |
| `choices.<name>` | Optional choice in the tool configuration. It needs `configuration`. |

An unsupported kind or value gives `unsupported_tool_definition`. Unknown fields are invalid.

## Hooks

Each hook has a distinct `name`, optional literal `args`, optional `types_or`, and `output`. Use `concise` for `file:line:column: rule message` findings. Use `plain` for pass, fail, or modified results without structured findings. Use `{configuration}` or `{<files key>}` as a whole argument to pass a generated file. Other braces are invalid. Arguments are passed without a shell.

## Sources and executables

A `pre-commit-repository` source has an HTTPS `repository` and a `tag` pattern containing `{version}`. The URL has no credentials, query, or trailing slash. The generated `policy.lock` records the upstream commit behind each reviewed tag. A Python hook uses pre-commit's Python.

A `release-binary` source also has `archive`, `checksums`, and `platforms`. The archive pattern contains `{platform}` and may contain `{version}`. The checksum filename may contain `{version}`. Map at least one of `linux-x64` and `macos-arm64` to its upstream platform name. Set `executable.name` to the bare program name.

The optional `executable.check_at_commit` defaults to `false`. When true, the policy lock records the program's SHA-256 and the commit hook checks an installed copy. The check does not run in `gateseal run` or the CI gate. mise verifies an archive's recorded SHA-256 when it downloads the archive.

Release binaries run through `mise --no-config exec --deny-env`. A hook from a `pre-commit-repository` source receives the caller's environment. Review a tool's arguments and behavior separately. For example, a scanner may read a repository's ignore file.

## Choices

A choice names a top-level configuration `key` and a `since` map. The map gives each accepted value and the earliest tool release that supports it. Values use lowercase letters and digits. Expose the choice and its default in `[capabilities.<name>.choices]` in `policy.toml`. The reserved key `extend` is invalid.
