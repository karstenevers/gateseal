# Upgrade and roll back

Use `gateseal upgrade` to change the policy, a tool version, or the gateseal
release recorded in a repository. To roll back, select the earlier version
with the same command.

## Common upgrades

Run the command from the repository root. Change the policy or a tool with
the gateseal version in the current Lock. Choose the example that matches your
change.

**Update the current team policy.** If your repository already uses this Git
policy, specify the new release tag. To switch from the bundled policy,
follow [Adopt a team policy](adopt-a-team-policy.md#select-a-release).

```sh
gateseal upgrade --policy-tag v2026.12
```

**Select another reviewed tool version.** For example, if your policy
reviews Betterleaks 1.8.0, record why you need that version and when you
will review the exception:

```sh
gateseal upgrade betterleaks=1.8.0 \
  --reason "1.9.0 flags the test fixtures" --review-by 2026-12-15
```

**Return that tool to the policy pin.** For the example above, if the policy
pins Betterleaks 1.9.0, run:

```sh
gateseal upgrade betterleaks
```

**Change the gateseal release.** Install the release you move to, newer or
older, and run its command with no target:

```sh
uv tool install --python 3.14.10 gateseal==0.2.0
gateseal upgrade
```

[Move to another gateseal release](#move-to-another-gateseal-release) explains
what the move reads and writes.

Review the changed files, stage them together, and commit. After a gateseal
move, each developer installs the same release and runs `gateseal setup`. The
first commit after an upgrade installs the tools that changed.

## Usage

```text
gateseal upgrade [COMPONENT[=VERSION] ...]
                 [--policy-tag TAG] [--policy URL | --policy bundled]
                 [--policy-path PATH] [--policy-commit COMMIT]
                 [--reason TEXT] [--review-by DATE]
```

Put component names before flags. Use `--repo` to specify a worktree,
`--json` for a structured result, or `--verbose` for more detail. With no
target, `gateseal upgrade` reports the current selection.

## Targets

| What you want to change | What to select |
| --- | --- |
| Git policy | Use `--policy-tag` for a release in the current repository. To change the repository or directory, use `--policy` or `--policy-path`. Use `--policy-commit` to require an exact commit for the tag. |
| Bundled policy | Use `--policy bundled` for the policy that comes with the running gateseal. |
| Tool | Use `COMPONENT=VERSION` for an exact reviewed version. A version different from the policy pin needs a reason and review date. Selecting the pin explicitly removes the exception. |
| gateseal | Install the target release and run `gateseal upgrade` with it. The release also fixes the pre-commit version. |

A bare component name such as `betterleaks` normally follows the policy
pin. If the current version is newer and the target policy still reviews it,
gateseal keeps that version. If this creates an exception, supply a reason
and review date. To force a rollback to the pin, specify its exact version.

For a Git policy, gateseal checks the tag against the commit when it first
adopts the release. When the Lock already records the release, gateseal uses
the stored copy. See [policy acquisition](policy-acquisition.md).

Move gateseal on its own or together with `--policy bundled`. Run other
policy or tool upgrades with the new release afterwards. To change pre-commit,
move to a gateseal release that carries the version you need.

## Steps

gateseal checks that the Manifest matches the Lock, then checks the requested
versions and all existing exceptions against the target policy. If an
exception no longer fits, name the affected component in the same command
and select a reviewed version.

gateseal prepares the new Manifest, Lock, and generated files before writing
to the worktree. It preserves unrelated Manifest text. If the change would
overwrite an edited generated file, it stops with `ownership_conflict`.
It writes the Lock last and checks the result. You decide when to stage and
commit the changes.

### An interrupted upgrade

If the Manifest still matches the old Lock, run the upgrade again. If the
new Manifest was written before the Lock, the next command can report
`lock_mismatch`. For a policy or component upgrade, run `gateseal configure`
to finish it.

For an interrupted gateseal move, review the diff first. Save any edits you
need to keep. Use `git restore` to restore `gateseal.toml` and the affected
generated files to their pre-upgrade contents. Then run the upgrade again.

## Move to another gateseal release

When the Lock pins another gateseal release, `gateseal upgrade` compiles the
new Lock from the Manifest and the policy alone. Of the old Lock it reads only
the ownership ledger: its format, its gateseal pin, the Manifest's SHA-256,
the policy's content digest and the files it lists. The listed files decide
which files the upgrade may replace or delete. A listed file that the running release never writes stops
the upgrade with `ownership_conflict`.

The new Lock pins the running release by the commit of its release tag, which
gateseal reads with your Git. The report names the gateseal move alone, so
review the Lock's diff for the rest.

If the new release bundles a different policy, even one with the same
release label, the upgrade reports `bundled_policy_moves`. Review that policy. To adopt it with the release, add
`--policy bundled`.

To roll back a gateseal move, revert its commit with `git revert`. When later
commits make the revert conflict, install the earlier release and run
`gateseal upgrade` with it.

A Lock of another format stops every command with `unsupported_schema`. gateseal
0.1.0 wrote format 1: delete `gateseal.lock` and the files gateseal wrote, then
run `gateseal configure` again.

## Results

`gateseal upgrade --json` lists the changed paths. Each `upgrade` diagnostic
names a target and reports its state and its values before and after the
change. An `exception` diagnostic identifies the component, version, and
whether the exception was added, changed, removed, retained, or made
redundant. A redundant exception remains until you remove it.

| Result | Next step |
| --- | --- |
| `upgraded` or `unchanged` (exit 0) | Review any changed files. |
| `incompatible_exception`, `unknown_version`, `invalid_exception`, `unsupported_component`, `runtime_target` (exit 2) | Check the target policy, supported components, and exception fields. |
| `bundled_policy_moves` (exit 2) | Review the policy that the new release bundles, then add `--policy bundled`. |
| `upstream_unavailable` (exit 4) or `upstream_tag_missing` (exit 2) | Check that Git reaches gateseal's repository and that it has the running release's tag. |
| `unsupported_schema` (exit 2) | Follow the next step for the Lock's format. |
| `invalid_invocation`, `invalid_field`, `manifest_edit_unsupported`, `ownership_conflict`, `lock_mismatch` (exit 2) | Check the flags and the named files. Editable policy and exception sections need ordinary TOML tables. |

See [Manifest and Lock](policy-contract.md) for field definitions and
[upgrade tests](what-the-tests-prove.md#upgrades) for the cases covered.
