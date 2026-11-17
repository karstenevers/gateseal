# Manifest and Lock

Edit `gateseal.toml` to select a policy and its checks. Run
`gateseal configure` and review `gateseal.lock` and the generated files
before committing. The Lock records the exact results of that selection.

## Manifest

The Manifest is UTF-8 TOML, schema 1. This example selects the bundled
policy's file hygiene and Python checks.

```toml
schema = 1
capabilities = ["hygiene", "python"]

[policy]
source = "bundled"
release = "2026.11-team-tools"

[choices.python]
target_version = "py311"

[exceptions]
```

Keep the `choices` and `exceptions` tables even when they are empty.

### Capabilities

Select at least one capability. Each name must appear in the selected
policy, and each name can appear only once. The bundled policy offers
`hygiene`, `python` and `secrets`. gateseal runs tools in the order the policy
defines. It does not infer capabilities from file types. An empty selection,
a repeated name, or a name the policy does not offer fails with
`unsupported_capability`.

### Policy

For a bundled release, set `source = "bundled"` and use the `release` label
in the running compiler. For a Git release, set `source = "git"` and supply
all four fields below.

| Field | Accepted value |
| --- | --- |
| `repository` | HTTPS URL without user information, or SSH URL with an optional username. Both accept a port and a repository path. Passwords, queries, fragments, dot segments, local paths, and scp-style URLs are invalid. |
| `path` | Directory relative to the repository root. Use `.` for the root. |
| `commit` | Full, lowercase, 40-character Git commit ID. |
| `tag` | Release tag that points to that commit at adoption. |

An invalid `repository`, `commit`, or `tag` fails with `invalid_policy_source`.

gateseal compares repository URLs exactly. For example, adding `.git` to a
URL requires a new adoption. See [Adopt a team policy](adopt-a-team-policy.md).

### Choices

Use `[choices.<capability>]` to set a choice the selected policy exposes.
An omitted choice uses its policy default. The bundled policy offers these
choices:

| Choice | Effect |
| --- | --- |
| `hygiene.exclude` | Skip a list of unique repository-relative paths and their contents for hygiene checks. Use letters, digits, dots, underscores, and hyphens within each path segment. Separate segments with `/` and omit the trailing slash. |
| `python.target_version` | Set Ruff's target Python version for syntax, rules, and import sorting. The bundled policy defaults to `py311`. gateseal reads this choice independently of the project's `requires-python`. |

The tool definition supports `py37` through `py315` where the selected Ruff
version accepts them. `py313` requires Ruff 0.4.5, `py314` requires 0.11.8,
and `py315` requires 0.14.11 or later. An unsupported value produces
`invalid_choice`. The bundled `secrets` capability has no choices.

### Skip optional hooks

A policy can mark hooks as optional. It then exposes the choice `skip` for
their capability, and a repository lists the hooks it leaves out:

<!-- manifest tables: skip -->
```toml
[choices.hygiene]
skip = ["check-yaml"]
```

The generated configuration lacks a skipped hook, so a commit, `gateseal run`
and the CI gate all skip it. A tool left without hooks writes no file and has
no Lock entry, and the CI gate runs no history scan of it. `gateseal status`
prints the skipped hooks as a choice. The bundled policy marks no hook as
optional.

A hook that the policy does not mark as optional gives `invalid_choice`. A
capability without optional hooks gives `unsupported_choice`.

### Repository hooks

Add `[hooks.<id>]` to run a script of the repository as a check. This table
runs `scripts/check-migrations.sh --strict` with the staged files under
`migrations/`:

<!-- manifest tables: hooks -->
```toml
[hooks.check-migrations]
name = "Check migration numbers"
run = "scripts/check-migrations.sh"
args = ["--strict"]
files = "^migrations/"
```

| Field | Accepted value |
| --- | --- |
| `<id>` | Lowercase letters, digits, and `-`, starting with a letter. |
| `name` | The name that pre-commit prints for the hook. |
| `run` | The path of a file in the repository, from its root, with `/` between segments. Each segment holds letters, digits, `.`, `_`, and `-`, and cannot be `.` or `..`. |
| `args` | Optional. Arguments that come before the file names. |
| `files` | Optional. A Python regular expression for the paths that start the hook. Without it, every staged file starts it. |

Repository hooks run after every policy hook, in the order of the Manifest,
at a commit, in `gateseal run` and in the CI gate. pre-commit passes the
matching staged files as arguments, and a script that exits with another
status than 0 fails the hook. Commit the script with a `#!` line, such as
`#!/bin/sh`, and the executable bit. gateseal installs nothing for it, so it
runs with the tools of the developer's machine and the CI runner.
`gateseal status` lists each repository hook, and `gateseal run` reports its
results with the origin `repository`.

A repository hook only adds a check. An ID or a name that a policy hook,
another repository hook or gateseal's own `gateseal-check` hook uses gives
`duplicate_identifier`. So does
a policy upgrade that adds a hook with a repository hook's ID. Each of these
gives `invalid_field`:

- A table without `name` or `run`.
- A `run` that is not a file in the repository.
- A field that the table above does not list, such as `stages`.
- A `files` value that is not a regular expression.

A tool from outside the repository belongs in the policy. See
[Add a tool](add-a-tool.md).

### Version exceptions

Add `[exceptions.<component>]` to select another version the policy has
reviewed. The component must belong to a selected capability.

| Field | Required value |
| --- | --- |
| `version` | Reviewed version, as a string. The resulting versions must satisfy each other's requirements. |
| `reason` | One printable line, at most 500 characters. |
| `review_by` | TOML local date, such as `2026-12-15`, without quotes or a time. |

An overdue date or a version equal to the policy pin produces a warning.
The exception still applies. pre-commit belongs to the compiler runtime.
An exception for it produces `runtime_exception`.

The optional [CI settings](github-ci.md) belong in `[ci]`. To change rules
that the policy does not expose as choices, change the policy.

### Configure flags

With a new repository, specify at least one `--capability`. configure pins
the running gateseal by the commit of its release tag. The policy defaults to
the bundled release.
For a Git policy, supply `--policy` and `--policy-tag`. The path defaults to
`.`, and the commit defaults to the commit at the tag during adoption.
See [Configure a repository](configure-a-repository.md).

With an existing Manifest, omitted options keep its settings. Supplied
capabilities must match the complete selected set, and source flags must
match the Manifest. Conflicting flags produce `configure_assertion_failed`.
Edit the Manifest and run configure again. Configure preserves an existing
gateseal pin.

## Flat policy

| File in a policy release | Contents |
| --- | --- |
| `policy.toml` | Schema-4 policy, including its capabilities, reviewed tool versions, and choices. See [Policy format](policy-format.md). |
| `rules/` | Rule files named by the policy. |
| `tools/` | [Team-defined tool definitions](tool-definitions.md). |
| `policy.lock` | Exact upstream facts for the reviewed versions. See [Policy Lock](policy-lock.md). |

## Lock

`gateseal.lock` is generated JSON. Use these fields when reviewing a change.

| Field | Contents |
| --- | --- |
| `schema` | The Lock format, integer `2`. |
| `manifest_sha256` | SHA-256 of the Manifest bytes after line-ending normalization. |
| `gateseal` | gateseal's `repository`, its `version`, and the full `commit` that the version's release tag names. |
| `policy` | `kind`, `release`, and content `digest`. A Git policy also records `repository`, `path`, `commit`, and `tag`. |
| `components` | Selected hook repositories. |
| `native_artifacts` | Each selected release binary's `version`, `minimum_mise`, and download records by platform. For an executable checked at commit, each record also holds the program's `executable_sha256`. |
| `choices` | Values for selected capabilities, including defaults. |
| `exceptions` | Each component's selected `version`, policy `pin`, `reason`, and `review_by`. |
| `outputs` | Each generated path, its SHA-256, UTF-8 encoding, and `executable: false`. |
| `inputs_fingerprint` | SHA-256 of the prefix `gateseal-inputs-v1`, a NUL byte, and the canonical JSON for the direct inputs before `outputs` and `inputs_fingerprint`. |

Each hook repository record has `repository`, `version`, `revision`, and
`hook_ids`.

### The ownership ledger

Every gateseal release reads five fields of any Lock: `schema`, `gateseal`,
`manifest_sha256`, the `digest` in `policy`, and `outputs`. Together they are
the ownership ledger. The release that wrote a Lock owns its other fields.

gateseal reads `schema` first. A Lock of another format produces
`unsupported_schema`, and the format changes only when the ledger changes.
Then gateseal reads the pin. A different release produces
`selected_compiler_required` from every command except `gateseal upgrade`,
which moves the Lock to the running release, as
[Move to another gateseal release](upgrade.md#move-to-another-gateseal-release)
explains.

## Owned outputs

| Generated file | What to review |
| --- | --- |
| `.pre-commit-config.yaml` | gateseal's repository at the pinned commit with its `gateseal-check` hook, then the selected hooks in policy order, each with its stages, then the repository hooks under `local`. The entry of a release binary names its version and each platform's URL and checksum. The file uses JSON syntax that pre-commit accepts as YAML. |
| `.gateseal/ruff.toml` | The selected target version and the policy's Ruff rules. |
| `.gateseal/betterleaks.toml` | The policy's secret detection rules. |
| `.gateseal/betterleaks-report.tmpl`, `.gateseal/betterleaks-history-report.tmpl` | Redacted findings. The history report also names the commit. |
| `.github/workflows/gateseal.yml` | The CI workflow when enabled. |
| `.gateseal/<tool>.<format>`, `.gateseal/<tool>-<key>.<extension>` | Configuration and fixed files declared by a team-defined tool. |

### Bundled hooks

Hygiene hooks skip paths listed in `hygiene.exclude`. The exclusions apply
only to tools in that capability.

Ruff's `ruff-check` applies safe fixes and reports findings with file, line,
column, and rule. `ruff-format` formats matching files. Both use
`.gateseal/ruff.toml` for Python files, stubs, and notebooks, including
files under directories with their own Ruff configuration.

The Betterleaks hook starts `betterleaks` through mise, which installs the
version in the hook's entry at the first use. At each commit, the
`gateseal-check` hook compares the installed program with the SHA-256 that
the Lock records.
It ignores repository ignore files and inline allow comments. Its report
shows the location and rule without the line of source that held the
secret. Both Ruff and Betterleaks treat filenames that start with a hyphen
as file arguments.

### File ownership and format

Configure validates paths before writing. It refuses a symlink in a managed
path, a conflicting unmanaged file, or an edited generated file. When a
selection drops an output, configure removes it only if the existing bytes
match the previous Lock. Configure replaces individual files atomically and
writes the Lock last. You can retry an interrupted configure.

Generated text uses LF line endings. Validation normalizes CRLF to LF for
the Manifest, Lock, and owned outputs. These files must be readable, regular,
non-executable files. Staged files must have Git mode `100644`.

The schemas reject unknown fields. JSON with duplicate keys or nonfinite
numbers is invalid. A Boolean is invalid where a schema version is required.
See [Commands and results](commands-and-results.md) for diagnostics.
