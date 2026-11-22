# Policy format

A policy directory contains `policy.toml`, its rule files, optional tool definitions, and generated `policy.lock`. To create a complete policy from the bundled example, run this in a directory without a `policy/` entry:

```sh
gateseal policy init policy
```

See [Author a team policy](author-a-policy.md) for editing and release steps. A repository selects a release of it in its [Manifest](policy-contract.md).

## What you edit

| File | Who writes it |
| --- | --- |
| `policy.toml` | `gateseal policy init` creates it. You edit it. `policy add` and `policy pin` change only their own lines. |
| `rules/` | `policy init` creates the bundled rules. Edit them for your team. |
| `tools/<name>.toml` | Optional. Write one only to add a tool that gateseal does not ship. |
| `policy.lock` | `gateseal policy init`, `lock` and `add` write it. Never edit it. |

## Example

This small policy offers four capabilities. Repositories choose from them. The comments say what each part does.

<!-- policy tables: team -->
```toml
schema = 5
release = "team-2026.11"
minimum_compiler = "0.2.0"

# Capability "hygiene": file checks. A repository may skip check-yaml.
[capabilities.hygiene]
optional = ["check-yaml"]

[capabilities.hygiene.tools.pre-commit-hooks]
hook_ids = ["trailing-whitespace", "end-of-file-fixer", "check-yaml"]
pin = "6.0.0"                # the version repositories use
reviewed = ["6.0.0", "5.0.0"] # versions the team has checked

[capabilities.hygiene.choices]
exclude = []                 # repositories may list paths to skip
skip = []                    # repositories may list optional hooks to skip

# Capability "python": Ruff with the team's rules in rules/ruff.toml.
[capabilities.python.tools.ruff]
hook_ids = ["ruff-check", "ruff-format"]
configuration = "rules/ruff.toml"
pin = "0.17.5"
reviewed = ["0.17.5", "0.17.1"]

[capabilities.python.choices]
target_version = "py311"     # default that a repository may change

# Capability "python-types": Pyright with the team's rules, for uv projects.
[capabilities.python-types.tools.pyright]
configuration = "rules/pyright.toml"
pin = "1.1.414"
reviewed = ["1.1.414"]

# Capability "secrets": Betterleaks with the team's rules.
[capabilities.secrets.tools.betterleaks]
configuration = "rules/betterleaks.toml"
pin = "1.10.1"
reviewed = ["1.10.1", "1.9.0", "1.8.0"]
```

A repository selects this policy in its Manifest. [Manifest and Lock](policy-contract.md#example) shows the matching Manifest.

[How gateseal works](architecture.md#how-the-policy-manifest-and-lock-connect) shows how the policy, the Manifest and the Lock connect, with excerpts of all three.

## Policy fields

`policy.toml` uses schema `5`. It needs a printable `release` label of at most 128 characters and `minimum_compiler` in `major.minor.patch` form. It needs at least one capability and one tool per capability. Tools and capabilities run in file order.

Optional `time_budget_seconds` sets the CI gate's [time budget](ci-gate.md#time-budget) in whole seconds, such as `300` for five minutes. A slower gate warns but never fails.

| Field under `capabilities.<name>` | Meaning |
| --- | --- |
| `tools.<tool>.hook_ids` | Optional ordered, distinct hook IDs. Omit it for every hook in definition order. |
| `tools.<tool>.configuration` | Relative path under `rules/`. Required when the tool definition declares a configuration. |
| `tools.<tool>.pin` | Default version, which must be reviewed. |
| `tools.<tool>.reviewed` | Distinct reviewed versions in `major.minor.patch` form. |
| `tools.<tool>.requires.<version>` | Optional map of other tools to minimum versions. |
| `alternatives.<choice>` | Two or more distinct hooks that offer one choice. |
| `optional` | Distinct hooks the repository may skip. |
| `choices.<choice>` | Exposed choice and its default. |

Capability and tool names match `[a-z][a-z0-9-]*`, and choice names match `[a-z][a-z0-9_]*`. gateseal checks the schema and minimum compiler before other fields. A mismatch gives `unsupported_schema` or `incompatible_compiler`. The compiler supplies definitions for `pre-commit-hooks`, `ruff`, `pyright`, `coverage`, `crap`, `pytestmut`, `uv-lock`, `uv-audit`, `deptry`, and `betterleaks`. Other definitions belong in `tools/<name>.toml`; see [Tool definitions](tool-definitions.md). A missing definition gives `unknown_tool`. A reserved name gives `reserved_tool_name`. Duplicate hooks, names, outputs, or tool assignments give `duplicate_identifier`.

### Alternatives

This fragment offers two formatters defined by the policy:

<!-- policy tables: alternatives -->
```toml
[capabilities.python.alternatives]
formatter = ["ruff-format", "black"]

[capabilities.python.choices]
formatter = "ruff-format"
```

Only the chosen hook runs. An alternative must name at least two distinct hooks and have a default. A hook cannot appear in multiple alternatives or be optional. An unknown hook gives `unsupported_hook`, a hook in more than one alternative gives `duplicate_identifier`, and a name that conflicts with a tool or compiler choice gives `invalid_field`. An invalid default gives `invalid_field` or `invalid_choice`. See [Add a tool](add-a-tool.md#offer-black-as-a-formatter) for a complete example.

### Optional hooks

This fragment lets a repository skip `check-yaml`:

<!-- policy tables: optional -->
```toml
[capabilities.hygiene]
optional = ["check-yaml"]

[capabilities.hygiene.choices]
exclude = []
skip = []
```

The `skip` default is empty. A repository can skip only a hook named in `optional`. A missing `skip` default gives `invalid_field`. Exposing `skip` without optional hooks gives `unsupported_choice`. If no hook remains for a tool, gateseal omits its generated files and Lock entry. A version exception for that tool gives `unused_exception`.

### Choices and rules

A policy can expose a tool's declared choice, an alternative, `skip`, or `exclude`. `exclude` skips paths for the entire capability. A capability with a history scan or a tool with the `project` scope cannot expose `exclude`. A rule file cannot set a key that the policy exposes as a choice.

Rule files use UTF-8 TOML and LF line endings. They must be regular, non-executable files under distinct relative paths. Path segments use lowercase letters, digits, dots, underscores, and hyphens. A segment cannot be `.` or `..`. A Betterleaks or Gitleaks rule file can have an `extend` table with only `useDefault` and `disabledRules`. It then starts from the rules built into the tool and turns off the rules that `disabledRules` names. Any other `extend` is invalid, because it could load another file. A Pyright rule file cannot set `extends` in `[tool.pyright]`, for the same reason. Either gives `invalid_field`.

Each release is flat. The keys `extends`, `extend`, `inherit`, `include`, and `base` give `policy_inheritance`. A pin must be reviewed and meet the other selected tools' requirements. Incompatible pins give `incompatible_policy`.

## Policy lock

`policy.lock` is generated JSON. It records the upstream Git commit, the archive URL and SHA-256, or the PyPI package for each reviewed version. `gateseal policy check` validates the file offline. `gateseal policy lock` and `gateseal policy add` check upstream data and download new archives before writing. An executable marked `check_at_commit` also gets an executable SHA-256.

| Code | Meaning |
| --- | --- |
| `policy_lock_missing`, `policy_lock_stale`, `policy_lock_unreviewed`, `policy_lock_edited` | The file is missing or does not agree with the policy. Run `gateseal policy lock`. |
| `upstream_changed` | A recorded tag, checksum or package changed. Investigate before accepting it. |
| `upstream_tag_missing`, `upstream_asset_missing`, `upstream_checksum_missing`, `upstream_release_missing` | The expected upstream release data is missing. |
| `upstream_checksum_mismatch`, `upstream_executable_missing` | The downloaded archive failed verification or lacks the expected executable. |
| `upstream_unavailable` | Git or an HTTPS download failed. Check access. Retry. |
| `policy_changed` | A policy file changed during the operation. Review it. Retry. |
| `invalid_invocation`, `unknown_component`, `version_unreviewed`, `requires_conflict`, `policy_edit_unsupported` | Check the component, reviewed version, requirements, or TOML layout in the diagnostic. |

See [Author a team policy](author-a-policy.md#review-a-tool-version) for review and recovery steps.

## Policy digest

The digest is a fingerprint of all policy files: `policy.toml`, the named rule files, the named tool definitions, and `policy.lock`. The same content always gives the same digest. Any change to these files, such as an edit or a new, removed or renamed file, gives a new digest. The digest identifies the content. It does not prove who published it.

The digest is a SHA-256 over each file's path and content, in path order. For the exact byte format, see `content_stream` in [`policy.py`](https://github.com/karstenevers/gateseal/blob/main/src/gateseal/policy.py).
