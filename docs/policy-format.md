# Policy format reference

Every policy release uses the same directory format as the bundled policy.
Edit `policy.toml`, the rule files it names under `rules/`, and any
[team-defined tools](../GLOSSARY.md#team-defined-tool) under `tools/`.
Generate `policy.lock` with `gateseal policy lock`. To start from the
bundled policy, use `gateseal policy init`. See the
[schema reference](policy-contract.md) for how a repository selects a release.

This `policy.toml` defines one capability. Repositories that select it check
YAML files with the `check-yaml` hook. The tool `pre-commit-hooks` is the
project pre-commit/pre-commit-hooks.

<!-- policy example -->
```toml
schema = 5
release = "team-2026.11"
minimum_compiler = "0.1.0"

[capabilities.hygiene.tools.pre-commit-hooks]
hook_ids = ["check-yaml"]
pin = "6.0.0"
reviewed = ["6.0.0"]

[capabilities.hygiene.choices]
exclude = []
```

| Field | Purpose and constraints |
| --- | --- |
| `schema` | Integer `5`. |
| `release` | A nonempty, printable label of up to 128 characters. Labels have no sort order. |
| `minimum_compiler` | The earliest compatible compiler version in `major.minor.patch` form. gateseal compares the numbers. |
| `capabilities.<name>.tools.<tool>` | One table for each tool of the capability. The tools run in file order. A capability name must match `[a-z][a-z0-9-]*`. gateseal processes capabilities in file order. |
| `capabilities.<name>.tools.<tool>.hook_ids` | Optional. Ordered, distinct hook IDs. Omit it to run all hooks in definition order. |
| `capabilities.<name>.tools.<tool>.configuration` | Path to a file under `rules/` with the extension the tool declares. Required exactly when the tool definition declares a configuration. |
| `capabilities.<name>.tools.<tool>.pin` | The default version. It must be one of the reviewed versions. |
| `capabilities.<name>.tools.<tool>.reviewed` | Every version the policy's authors reviewed, each once. |
| `capabilities.<name>.tools.<tool>.requires.<version>` | Optional. The minimum versions of other tools that a reviewed version needs. |
| `capabilities.<name>.alternatives.<choice>` | Optional. Two or more distinct hooks from this capability. Expose a choice with a default to [select one of them](#alternatives). |
| `capabilities.<name>.choices.<choice>` | Exposes a choice with its default. Use the compiler's [`exclude`](#exclude), a tool's declared choice, or a capability's alternatives. Omit the table if the capability exposes none. |

## Capabilities

A [capability](../GLOSSARY.md#capability) groups checks by what they apply to.
A policy defines at least one capability, and a manifest selects at least
one of them. Put checks for any file under `hygiene`, as the bundled policy
does. Group all checks for one language under its name, such as `python` for
linting and formatting. Use a separate capability for a check that spans file
types, such as `secrets`.

A repository selects capabilities in `gateseal.toml`. When it adopts a new
release, it gets the tools, rules, and pins for those capabilities.
For example, adding Gitleaks to `secrets` enables it in repositories that
already select `secrets` when they upgrade. A
[Version Exception](../GLOSSARY.md#version-exception) can keep a tool on
another reviewed version. See [Add a tool](add-a-tool.md).

### Alternatives

Offer alternative hooks when repositories need to choose one tool for a job.
For example, these tables offer two formatters in a policy that defines both
Ruff and Black.

<!-- policy tables: alternatives -->
```toml
[capabilities.python.alternatives]
formatter = ["ruff-format", "black"]

[capabilities.python.choices]
formatter = "ruff-format"
```

Only the selected formatter runs. If the choice leaves a tool with no hooks,
gateseal skips its configuration and Lock entry. A Version
Exception for that tool gives `unused_exception`. To see which formatter a
repository selected, run `gateseal status` or `gateseal doctor`.

| Problem | Code |
| --- | --- |
| The alternative names a hook the capability does not run. | `unsupported_hook` |
| A hook appears in more than one choice. | `duplicate_identifier` |
| A choice lacks two distinct hooks or a default. Its name can also conflict with a tool or compiler choice. | `invalid_field` |
| The manifest selects a value outside the list of hooks. | `invalid_choice` |

## Tools

Every tool belongs to exactly one capability, as a table under its
`tools`. The compiler supplies definitions for `pre-commit-hooks`, `ruff`,
and `betterleaks`. A team supplies other definitions as
[`tools/<name>.toml`](tool-definitions.md). An error in a definition names
its file.

| Problem | Code |
| --- | --- |
| A selected tool has no built-in or team definition. | `unknown_tool` |
| A team definition uses a built-in tool's name or a tool uses the name `pre-commit` or `python`. | `reserved_tool_name` |
| Tools share a hook ID, hook name, owned output, or hook repository. Two capabilities can also hold one tool. | `duplicate_identifier` |
| The policy defines no capability, a capability has no tools, or `policy.toml` has a top-level table other than `capabilities`. | `invalid_field` |
| A `hook_ids` entry is missing from the definition. | `unsupported_hook` |

Each tool choice needs a unique name within its capability. The compiler
reserves `exclude` in every capability.

### Exclude

A capability that exposes `exclude` lets a repository list paths in
`[choices.<capability>]` that the hooks of every tool in that capability
skip. A capability that does not expose it refuses the choice. The bundled
policy exposes `exclude` only for `hygiene`.

A commit and the CI gate run the same hooks, so they skip the same paths. A
history scan reads every path, so a capability that holds a tool with a
history scan cannot expose `exclude` (`invalid_field`).

## Versions

Each tool has a pin and the versions that policy authors reviewed with the
rules and other tools. For Ruff, the version is the Ruff release installed
by the pinned `ruff-pre-commit` repository.

Compiler and tool versions have three nonnegative decimal numbers, for
example `6.0.0`. A number can only start with zero when it is zero.

A reviewed version can declare `requires."<version>" = { ruff = "0.17.1" }`
in its tool's table to require Ruff 0.17.1 or later whenever Ruff is also
selected. You can name another built-in tool or a tool in the policy. If the
pins fail to meet each other's requirements, gateseal reports
`incompatible_policy`.

The compiler supplies pre-commit. A policy cannot set or require its version.
gateseal checks whether its own pre-commit supports the selected hooks.
For example, a release binary uses pre-commit's `unsupported` language,
introduced in pre-commit 4.4.0. An incompatible selection gives
`incompatible_runtime`.

## Rule files

### Files a policy names

Rule files must be UTF-8 TOML with LF line endings. gateseal rejects
carriage returns and a top-level `extend` key that could load another
configuration.

Use distinct relative paths within the policy directory. Each path segment
can contain lowercase letters, digits, dots, underscores, and hyphens.
Paths cannot contain a `.` or `..` segment. The filename must use the
extension the field declares. Use regular files without execute permission.

### Choices and rule files

If a policy exposes a choice, its rule file cannot also set the same
top-level key (`invalid_field`). The selected tool version must accept the
choice's default (`invalid_choice`). For example, if you expose
`python.target_version`, omit `target-version` from `rules/ruff.toml`.
If the policy omits the choice, gateseal adds no target version of its own.

### The bundled rules

- Ruff enables `E4`, `E7`, `E9`, `F`, and `I`, sets an 88-character
  line length, and targets `py311`. The hook repository installs Ruff from
  PyPI by version.
- Betterleaks checks for AWS access key identifiers, classic and fine-grained
  GitHub tokens, and generic API key assignments. Assignment keys can be
  quoted, as in JSON.

### One flat release

Derive policies when you author them. Each published release must stand on
its own. The top-level keys `extends`, `extend`, `inherit`, `include`, and
`base` give `policy_inheritance`.

Loading reads a snapshot of the files without running tools. Later edits
cannot change that snapshot. Running the selected tools shows whether they
work together.

## Policy Lock

`policy.lock` must record the upstream data for every reviewed version.
gateseal checks it on every load. See the [Policy Lock reference](policy-lock.md)
for the file format and the `policy lock`, `policy add`, and `policy pin`
commands.

## Compatibility for policy authors

### Schemas

This compiler reads schema 5. The schema is stable.

### What a compiler reads first

gateseal checks `schema` first (`unsupported_schema`), then
`minimum_compiler` (`incompatible_compiler`). On either error, it tells you
which compiler to use and stops before reading other fields or writing files.

### Changes within a schema

An incompatible format change requires a new schema. Within a schema, a
newer compiler can add an optional field, another tool kind, or a new value
for a tool definition field. If your policy uses one, set `minimum_compiler`
to the first version that supports it.

### The policy digest

The digest is `sha256:` followed by the hexadecimal SHA-256 of the policy's
content stream. Compute the stream in this order.

1. Write `gateseal-policy-content-v1` and a NUL byte.
2. Sort the policy files by UTF-8 path. For each file, write its path length
   in bytes, its path bytes, its content length in bytes, and its content
   bytes. Encode both lengths as eight-byte, big-endian integers.

Include `policy.toml`, every rule file and tool definition it names, and
`policy.lock`. Exclude unrelated files, absolute paths, and modification
times. The digest identifies the content. Authenticating the publisher
requires separate evidence.
