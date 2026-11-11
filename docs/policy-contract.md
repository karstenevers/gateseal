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

Select `hygiene` explicitly. Each other name must appear in the selected
policy, and each name can appear only once. The bundled policy also offers
`python` and `secrets`. gateseal runs tools in the order the policy defines.
It does not infer capabilities from file types. A missing `hygiene`, a
repeated name, or a name the policy does not offer fails with
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

With a new repository, specify at least `--capability hygiene` and the compiler
wheel or source and its SHA-256. The policy defaults to the bundled release.
For a Git policy, supply `--policy` and `--policy-tag`. The path defaults to
`.`, and the commit defaults to the commit at the tag during adoption.
See [Configure a repository](configure-a-repository.md).

With an existing Manifest, omitted options keep its settings. Supplied
capabilities must match the complete selected set, and source flags must
match the Manifest. Conflicting flags produce `configure_assertion_failed`.
Edit the Manifest and run configure again. Configure and activation preserve
an existing compiler pin and source.

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
| `schema` | Integer `1`. |
| `manifest_sha256` | SHA-256 of the Manifest bytes after line-ending normalization. |
| `compiler` | `name`, `version`, wheel `filename`, `sha256`, and `source`. |
| `policy` | `kind`, `release`, and content `digest`. A bundled policy also records `compiler_sha256`. A Git policy records `repository`, `path`, `commit`, and `tag`. |
| `runtime` | The compiler's `minimum_uv`, `python`, and `pre_commit` installation records. See [Compiler runtime](compiler-runtime.md). |
| `components` | Selected hook repositories. When the policy selects a separate Hook Python for Python hooks, `python` records its version and platform downloads. |
| `native_artifacts` | Each selected release binary's `version`, `minimum_mise`, and download records by platform. |
| `choices` | Values for selected capabilities, including defaults. |
| `exceptions` | Each component's selected `version`, policy `pin`, `reason`, and `review_by`. |
| `outputs` | Each generated path, its SHA-256, UTF-8 encoding, and `executable: false`. |
| `inputs_fingerprint` | SHA-256 of the prefix `gateseal-inputs-v1`, a NUL byte, and the canonical JSON for the direct inputs before `outputs` and `inputs_fingerprint`. |

Each hook repository record has `repository`, `version`, `revision`,
`hook_ids`, and `tools`. The last field maps installed tool names to versions.
For example, Ruff's hook repository installs `ruff` at its selected version.

gateseal reads the compiler pin before the policy or other Lock fields. A
different compiler produces `selected_compiler_required`. The locked compiler
interprets the rest of the Lock.

### Compiler source

`compiler.source` records `kind` and `location`.

- For `local-wheel`, the location is an absolute path to the wheel.
- For `https-wheel`, use a wheel on `raw.githubusercontent.com`. The path
  must contain the owner, repository, full 40-character commit ID, and wheel
  path. The URL must have no user information, port, query, fragment, or dot
  segment.

You can supply a local wheel to activation if the recorded location is
unavailable. Its SHA-256 must match. The Lock keeps its original location.

## Owned outputs

| Generated file | What to review |
| --- | --- |
| `.pre-commit-config.yaml` | The selected hooks in policy order. The file uses JSON syntax that pre-commit accepts as YAML. |
| `.gateseal/ruff.toml` | The selected target version and the policy's Ruff rules. |
| `.gateseal/betterleaks.toml` | The policy's secret detection rules. |
| `.gateseal/betterleaks-report.tmpl`, `.gateseal/betterleaks-history-report.tmpl` | Redacted findings. The history report also names the commit. |
| `.gateseal/mise.toml` | Release binary versions, URLs, and checksums. |
| `.github/workflows/gateseal.yml` | The CI workflow when enabled. |
| `.gateseal/<tool>.<format>`, `.gateseal/<tool>-<key>.<extension>` | Configuration and fixed files declared by a team-defined tool. |

### Bundled hooks

Hygiene hooks skip paths listed in `hygiene.exclude`. The exclusions apply
only to tools in that capability.

Ruff's `ruff-check` applies safe fixes and reports findings with file, line,
column, and rule. `ruff-format` formats matching files. Both use
`.gateseal/ruff.toml` for Python files, stubs, and notebooks, including
files under directories with their own Ruff configuration.

The Betterleaks hook runs the prepared `gateseal-betterleaks` executable.
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
