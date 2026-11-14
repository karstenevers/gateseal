# Adopt a team policy

Use a team's reviewed checks in your repository. Ask the policy author for the
Git repository URL, the policy directory, the release tag, and the names of the
capabilities your repository needs. To publish your own release, see
[Author a team policy](author-a-policy.md) and [Add a tool](add-a-tool.md).

## Select a release

If your repository uses the bundled policy, switch to the team policy from
the repository root. Replace the example URL, directory, and tag with the
values from your policy author.

```sh
gateseal upgrade --policy https://github.com/team/gateseal-policy.git \
  --policy-path policy --policy-tag team-2026.11
```

If your repository already uses the same Git policy and directory, specify
only the new tag. See [Upgrade and roll back](upgrade.md#common-upgrades).

For a new repository, get the compiler pin from
[Pin the release in a repository](install.md#pin-the-release-in-a-repository).
From the repository root, run the configure command with the team's release
details. This example uses a team policy that offers the same three
capabilities as the bundled policy.

```sh
gateseal configure --capability hygiene --capability python --capability secrets \
  --policy https://github.com/team/gateseal-policy.git \
  --policy-path policy --policy-tag team-2026.11 \
  --compiler-source PYPI_WHEEL_URL --compiler-sha256 WHEEL_SHA256
```

gateseal records the commit in `gateseal.toml` and reports that the tag
matches it. A later change to the tag cannot change the selected commit.
Review and stage `gateseal.toml`, `gateseal.lock`, and the generated files.

## Give Git read access

Each contributor who runs `gateseal activate` needs read access to the policy
repository. Use a URL without credentials in `gateseal.toml`. For a private
policy, [set up Git access on your machine or in CI](authenticate.md).

`configure`, `activate`, `check`, and `upgrade` can fetch a missing or
damaged release. Commits and [other commands](policy-acquisition.md#which-commands-fetch)
use the worktree's stored copy.

## Set Policy Choices

Use `gateseal.toml` to set a choice the policy exposes. For example, if the
policy exposes `hygiene.exclude`, skip hygiene checks in these directories.

```toml
[choices.hygiene]
exclude = ["vendor", "docs/generated"]
```

If the policy exposes `python.target_version`, tell Ruff the oldest Python
version your code supports. The bundled policy defaults to `py311`.
gateseal does not read `requires-python`.

```toml
[choices.python]
target_version = "py39"
```

## Add a Version Exception

If the team reviewed another version of a selected tool, you can use it in
your repository. In `gateseal.toml`, record the version, why you need it,
and when to review it.

```toml
[exceptions.betterleaks]
version = "1.8.0"
reason = "1.9.0 flags the test fixtures (upstream issue 123)"
review_by = 2026-12-15
```

Each compiler release includes its own pre-commit. To change that version,
[upgrade the compiler](upgrade.md#common-upgrades).

After editing the manifest, run `gateseal configure`. You can also
[set an exception with gateseal upgrade](upgrade.md#common-upgrades).
Both commands check that the policy exposes each choice, that it reviews
each selected version, and that the versions meet each other's requirements.
If a check fails, the command stops before writing files.

## See which tools run

Run `gateseal status` to see the selected tools, choices, and exceptions.
`doctor`, `check`, and `configure` also report them. An overdue exception
stays active until you renew, change, or remove it.

The tool origin is `built-in` or `team-defined`. In `run --json`, look for
`origin` on each hook and finding. Both types use a pinned tool version.
For a team-defined scanner, check the tool's arguments to see whether a
repository's ignore file can suppress findings. See
[Tool definition guarantees](tool-definitions.md#what-a-team-definition-guarantees).
