# Write each tool once under its capability

Accepted on 2026-11-16. This keeps the catalogs of reviewed versions of [design record 0005](0005-version-policy-components-as-reviewed-catalogs.md) and changes only where `policy.toml` writes them.

A policy is a tree: a capability has tools, and a tool has hooks, a pin and its reviewed versions. Policy schema 4 wrote one section per kind of fact instead. A tool appeared in a capability's `tools` list, in a `tools` table with its hooks and rule file, and in a `components` table with its pin and versions, and each choice's default sat in a fourth section. A reader followed a tool's name across the file to learn what a capability runs, and three refusals existed only because the sections could disagree. Policy schema 5 writes each tool once, as `[capabilities.<capability>.tools.<tool>]` with `hook_ids`, `configuration`, `pin`, `reviewed` and `requires`. A capability's `choices` table gives each exposed choice its default in the shape a Manifest uses to set it. gateseal reads only schema 5, and neither reads nor converts an older policy. What a loaded policy holds is the same, so the Manifest, the Lock and the Policy Lock keep their formats.

The same change lets any capability expose `exclude`, which schema 4 reserved for a capability named `hygiene`. The policy's authors decide for each capability whether a repository may keep paths from its tools. A commit, `gateseal run` and the CI gate's hooks run one generated configuration, so they skip the same paths. The gate's history scan starts the scanner itself and reads every path. So a capability that holds a tool with a history scan cannot expose `exclude`, and a commit never skips a path that the gate still checks.

## Considered Options

- Write each tool once under its capability, in a new schema
- Keep schema 4 and its sections
- Read schema 4 as well, or convert it, which keeps two layouts in the reader
- Let a history scan skip excluded paths, which needs a field in every tool definition with a history scan that says how its scanner takes paths to skip
- Keep `exclude` for `hygiene` alone, which leaves a team that names its general checks otherwise, or wants to keep vendored code from Ruff, without it

## Consequences

- A policy's authors add a tool with one table, and every fact about a capability sits under headers that start with its name.
- A policy of schema 4 or older stops with `unsupported_schema`, and its authors rewrite it by hand.
- `gateseal policy add` and `gateseal policy pin` edit `reviewed` and `pin` in the tool's table and refuse a tool written as an inline table.
- A policy that exposes `exclude` for a capability with a history scan stops with `invalid_field`, which names the capability and the tool.
