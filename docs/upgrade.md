# Upgrade and roll back

Use `gateseal upgrade` to change a policy release, a reviewed tool version, or the gateseal release. Run it from the repository root. `gateseal.toml` is the Manifest that selects the checks. `gateseal.lock` is the Lock that records the selection and generated files.

To adopt a new tag of the same team policy, replace the example tag. Run:

```sh
gateseal upgrade --policy-tag team-2026.12
```

To select another version that the policy reviewed, record a reason and review date. This example assumes the policy offers Betterleaks 1.8.0:

```sh
gateseal upgrade betterleaks=1.8.0 \
  --reason 'Needed for the test fixtures' --review-by 2026-12-15
```

To return to the policy's pinned version, supply its exact version. A bare component follows the pin unless the selected version is newer and still reviewed.

To move gateseal itself, run `gateseal upgrade` using the target release. A bundled policy change can require `--policy bundled`. Change other policy and tool selections afterwards.

Review the changed Manifest, Lock, and generated files. Stage the files. Commit them together. A later commit builds any newly selected hook environments. If the release needs a newer pre-commit, each developer updates pre-commit or runs setup for that release.

## Recover from an interrupted upgrade

If the Manifest still matches the old Lock, retry the upgrade. If a policy or tool upgrade wrote the Manifest but not the Lock, run `gateseal configure` to finish it. If a gateseal release change stopped after writing the Manifest, save any changes you need. Restore the Manifest and affected generated files to their pre-upgrade contents. Then retry.

To roll back a committed upgrade, revert its commit. To select another policy source, see [Adopt a team policy](adopt-a-team-policy.md). Run `gateseal upgrade --help` for all options and see [Commands and results](commands-and-results.md) for exit codes.

## Upgrade errors

| Code | What to check |
| --- | --- |
| `incompatible_exception`, `unknown_version`, `invalid_exception` | Check the target policy and the named exception. |
| `bundled_policy_moves` | Review the bundled policy change. Add `--policy bundled` to accept it. |
| `ownership_conflict`, `manifest_edit_unsupported`, `lock_mismatch` | Review the named files and the proposed edits. |
| `upstream_tag_missing`, `upstream_unavailable` | Check the release tag and Git access. |
| `unsupported_schema` | Follow the reported step for the Lock format. |
