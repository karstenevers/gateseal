# Manifest and Lock

`gateseal.toml`, the Manifest, selects a policy and checks for one repository. `gateseal.lock`, the Lock, records the resolved selection and generated files. After editing the Manifest, run `gateseal configure`. Review the files together.

## What you edit

| Part | Who writes it |
| --- | --- |
| `gateseal.toml`: `schema`, `[policy]` | `gateseal configure` writes them at first. `gateseal upgrade` rewrites `[policy]` when it moves the policy. |
| `gateseal.toml`: `capabilities` | You choose them with `--capability` at first. Edit them later, then run `gateseal configure`. |
| `gateseal.toml`: `[choices]`, `[exceptions]` | Empty at first. Edit them only to leave a policy default. `gateseal upgrade` can write an exception. |
| `gateseal.toml`: `[hooks]`, `[ci]` | Optional. Add them when you need your own script or CI. `--ci` writes `[ci]` at first. |
| `gateseal.lock` | `gateseal configure` and `gateseal upgrade` write it. Never edit it. |
| `.pre-commit-config.yaml`, `.gateseal/`, the CI file | Generated with the Lock. Never edit them. |

## Example

This Manifest uses the team policy from [Policy format](policy-format.md#example). It selects three capabilities, skips one optional hook, and changes one choice. Everything else keeps the policy's defaults.

<!-- manifest tables: team -->
```toml
schema = 1
capabilities = ["hygiene", "python", "secrets"]

[policy]
source = "git"
repository = "https://github.com/team/gateseal-policy.git"
path = "policy"
commit = "3f2a9c1d5e7b4a60918c2d4e6f8a0b1c3d5e7f90"
tag = "team-2026.11"

[choices.hygiene]
skip = ["check-yaml"]        # the policy lists check-yaml as optional

[choices.python]
target_version = "py312"     # the policy default is py311

[exceptions]
```

You rarely write `commit` yourself. The `gateseal configure` command in [Adopt a team policy](adopt-a-team-policy.md) records it.

## Example with the bundled policy

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

Select at least one distinct capability from the policy. The bundled policy offers `hygiene`, `python`, `python-types`, `python-tests`, `python-crap`, `python-mutation`, `python-lock`, `python-audit`, `python-dependencies`, `secrets`, and [`typescript`](typescript.md). Checks run in policy order. gateseal does not infer capabilities from files. An invalid selection gives `unsupported_capability`. For a new repository, `configure` needs at least one `--capability`. In an existing repository, supplied options must match the Manifest. A conflict gives `configure_assertion_failed`.

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

`[choices.<capability>]` sets values that the policy exposes. Omitted values use policy defaults. The bundled `hygiene.exclude` and `typescript.exclude` choices list repository-relative paths to skip for the checks of that capability. Path segments use letters, digits, dots, underscores, and hyphens. The bundled `python.target_version` defaults to `py311` and sets Ruff's target independently of `requires-python`.

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
| `stages` | Optional. `["commit", "run", "ci"]`, `["push", "ci"]`, or `["ci"]`. The default is `["commit", "run", "ci"]`. |
| `scope` | Optional. `files`, the default, passes the matching paths. `project` passes no paths and runs the script once. |

Commit the script with a `#!` line and executable permission. gateseal does not install its dependencies. By default, a repository hook runs at commit, in `gateseal run`, and in the CI gate. Duplicate IDs or names give `duplicate_identifier`. Missing required fields, invalid paths, expressions or stages, and unknown fields give `invalid_field`.

Set `stages` to move a check that is too slow for each commit. This fragment runs the script at `git push` and in the CI gate, and not at commit or in `gateseal run`:

<!-- manifest tables: push hook -->
```toml
[hooks.slow-tests]
name = "Run the slow tests"
run = "scripts/slow-tests.sh"
stages = ["push", "ci"]
```

See [Checks at a push](work-in-a-configured-repository.md#checks-at-a-push) for what each clone needs.

Set `scope = "project"` for a script that checks or rebuilds the whole project, such as a code generator. With the default `files` scope, pre-commit can split many matching paths across several runs of the script at the same time. This fragment runs the generator once when a schema file changes:

<!-- manifest tables: project hook -->
```toml
[hooks.api-types]
name = "Check that the API types are fresh"
run = "scripts/generate-api-types.sh"
files = "^schema/"
scope = "project"
```

Without `files`, a project hook runs at every commit, also when the commit only deletes files. With `files`, it runs when a checked file matches: a staged file at a commit, a changed file in a CI gate run on a range, or any tracked file in `gateseal run` and in a CI run that [checks all files](ci-gate.md#what-the-gate-checks). A commit that only deletes matching files does not start it. A hook that changes a tracked file fails, so a freshness check needs only the generator in its script. A new file that Git does not track yet does not fail the hook.

### Version exceptions

`[exceptions.<component>]` selects another version the policy reviewed. The component must be selected. Set `version` to the reviewed version. Set `reason` to one printable line of at most 500 characters. Set `review_by` to an unquoted TOML date. An overdue exception or one equal to the pin produces a warning but still applies. The gateseal release controls pre-commit; an exception for it gives `runtime_exception`.

### CI

`[ci]` turns on the generated CI file. The keys need gateseal 0.3.0 or later, except `provider` and `default_branch`. See [GitHub CI](github-ci.md) and [Azure Pipelines](azure-pipelines.md).

| Key | Value | Default |
| --- | --- | --- |
| `provider` | `"github"` or `"azure"`. Required. | |
| `default_branch` | The branch whose pushes the gate checks. Required unless `standalone = false`. | |
| `branches` | More branches whose pushes the gate checks. An entry is a branch name, or a name that ends in `/*` for every branch below it, such as `"release/*"`. | `[]` |
| `schedule` | `true` adds a weekly run of `default_branch` on Monday at 04:37 UTC. GitHub runs it on the repository's default branch. | `false` |
| `timeout_minutes` | The job's time limit, a whole number from 1 to 360. | `30` |
| `pool` | Azure Pipelines only. The agent pool of your own agents. | The hosted `ubuntu-24.04` image |
| `runs_on` | GitHub only. The runner labels, such as `["self-hosted", "linux", "x64"]`. | The hosted `ubuntu-24.04` image |
| `standalone` | `false` writes only the job, for your own pipeline to include. Then leave out `default_branch`, `branches` and `schedule`. | `true` |

An invalid value gives `invalid_field` and names the key. An unsupported provider gives `unsupported_ci_provider`.

## Lock

The Lock is generated JSON with schema `2`. Review its `gateseal` repository, version and commit, and its `policy` release and digest. Review its `components`, `native_artifacts`, `choices`, `exceptions`, and `outputs`. Each output records its path and SHA-256. Release binary records include archive URLs and checksums by platform. A binary with commit-time verification also records the executable's SHA-256. A selection with a tool from npm records the policy's Node once, as `native_artifacts.node`, in the same form. The tool's record in `components` names its npm package, its version and its other `packages`.

The generated files include `.pre-commit-config.yaml`, selected files under `.gateseal/`, and the CI file when enabled. Do not hand-edit them. Configure refuses a conflicting or edited generated file with `ownership_conflict`. It writes the Lock last, so an interrupted operation can be retried. See [Troubleshoot gateseal](troubleshooting.md).

Generated files must be regular, readable, non-executable files. The Manifest, Lock, and generated text use LF line endings. Validation also accepts CRLF. Staged files need Git mode `100644`. Unknown fields, duplicate JSON keys, and nonfinite JSON numbers are invalid.
