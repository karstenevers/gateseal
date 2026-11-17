# Manifest and Lock

`gateseal.toml`, the Manifest, selects a policy and checks for one repository. `gateseal.lock`, the Lock, records the resolved selection and generated files. After editing the Manifest, run `gateseal configure`. Review the files together.

This Manifest selects the bundled hygiene and Python checks:

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

## Manifest

The Manifest is UTF-8 TOML with schema `1`. It needs `capabilities`, `policy`, `choices`, and `exceptions`. Keep the last two tables even when they are empty.

### Capabilities

Select at least one distinct capability from the policy. The bundled policy offers `hygiene`, `python`, and `secrets`. Checks run in policy order. gateseal does not infer capabilities from files. An invalid selection gives `unsupported_capability`. For a new repository, `configure` needs at least one `--capability`. In an existing repository, supplied options must match the Manifest. A conflict gives `configure_assertion_failed`.

### Policy

For the bundled policy, set `source = "bundled"` and use its release label. A Git policy uses `source = "git"` and these fields:

| Field | Value |
| --- | --- |
| `repository` | HTTPS URL without credentials, or SSH URL with an optional username. Local paths, queries, fragments, dot segments, and scp-style URLs are invalid. |
| `path` | Directory relative to the repository root. Use `.` for the root. |
| `commit` | Full lowercase 40-character Git commit ID. |
| `tag` | Release tag that points to the commit at adoption. |

An invalid `repository`, `commit`, or `tag` gives `invalid_policy_source`. URLs are compared exactly. See [Adopt a team policy](adopt-a-team-policy.md).

### Choices

`[choices.<capability>]` sets values that the policy exposes. Omitted values use policy defaults. The bundled `hygiene.exclude` choice lists repository-relative paths to skip for hygiene checks. Path segments use letters, digits, dots, underscores, and hyphens. The bundled `python.target_version` defaults to `py311` and sets Ruff's target independently of `requires-python`.

The definition accepts `py37` through `py315`; the selected Ruff release must support the target. `py313` needs Ruff 0.4.5 or later, `py314` needs 0.11.8 or later, and `py315` needs 0.14.11 or later. An unsupported value gives `invalid_choice`.

### Skip optional hooks

A policy can mark hooks optional and expose `skip`. For example, this fragment skips `check-yaml` if the policy permits it:

<!-- manifest tables: skip -->
```toml
[choices.hygiene]
skip = ["check-yaml"]
```

A skipped hook does not run at commit, in `gateseal run`, or in CI. The bundled policy marks no hook optional. A hook that is not optional gives `invalid_choice`. A capability without optional hooks gives `unsupported_choice`.

### Repository hooks

A repository hook adds a script from the repository after policy hooks. This fragment runs the script with matching paths after `--strict`:

<!-- manifest tables: hooks -->
```toml
[hooks.check-migrations]
name = "Check migration numbers"
run = "scripts/check-migrations.sh"
args = ["--strict"]
files = "^migrations/"
```

| Field | Value |
| --- | --- |
| Table ID | Lowercase letters, digits, and hyphens, starting with a letter. |
| `name` | Name displayed by pre-commit. |
| `run` | Path to a repository file. Segments use letters, digits, `.`, `_`, and `-`; `.` and `..` are invalid segments. |
| `args` | Optional arguments before the file paths. |
| `files` | Optional Python regular expression for matching paths. The default matches all staged files. |

Commit the script with a `#!` line and executable permission. gateseal does not install its dependencies. Repository hooks run at commit, in `gateseal run`, and in the CI gate. Duplicate IDs or names give `duplicate_identifier`. Missing required fields, invalid paths or expressions, and unknown fields give `invalid_field`.

### Version exceptions

`[exceptions.<component>]` selects another version the policy reviewed. The component must be selected. Set `version` to the reviewed version. Set `reason` to one printable line of at most 500 characters. Set `review_by` to an unquoted TOML date. An overdue exception or one equal to the pin produces a warning but still applies. The gateseal release controls pre-commit; an exception for it gives `runtime_exception`.

### CI

`[ci]` accepts `provider = "github"` and `default_branch`. See [GitHub CI](github-ci.md). An unsupported provider gives `unsupported_ci_provider`.

## Lock

The Lock is generated JSON with schema `2`. Review its `gateseal` repository, version and commit, and its `policy` release and digest. Review its `components`, `native_artifacts`, `choices`, `exceptions`, and `outputs`. Each output records its path and SHA-256. Release binary records include archive URLs and checksums by platform. A binary with commit-time verification also records the executable's SHA-256.

The generated files include `.pre-commit-config.yaml`, selected files under `.gateseal/`, and the GitHub workflow when enabled. Do not hand-edit them. Configure refuses a conflicting or edited generated file with `ownership_conflict`. It writes the Lock last, so an interrupted operation can be retried. See [Troubleshoot gateseal](troubleshooting.md).

Generated files must be regular, readable, non-executable files. The Manifest, Lock, and generated text use LF line endings. Validation also accepts CRLF. Staged files need Git mode `100644`. Unknown fields, duplicate JSON keys, and nonfinite JSON numbers are invalid.
