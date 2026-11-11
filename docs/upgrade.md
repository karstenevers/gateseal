# Upgrade and roll back

Use `gateseal upgrade` to change the policy, a tool version, or the gateseal
release recorded in a repository. To roll back, select the earlier version
with the same command.

## Common upgrades

Run the command from the repository root with the gateseal version in the
current Lock. Choose the example that matches your change.

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

**Change the gateseal release.** Set the [wheel
variables](install.md#pin-the-wheel-on-pypi) for the target release. Use
the currently pinned command to update the repository:

```sh
gateseal upgrade --compiler-source "$GATESEAL_WHEEL_URL" \
  --compiler-sha256 "$GATESEAL_WHEEL_SHA256" --activate
```

After a compiler upgrade, [install the new pinned
release](install.md#install-from-pypi) for subsequent commands. For other
upgrades, run `gateseal activate` to prepare the changed Lock, or include
`--activate` in the upgrade command. Review the changed files, stage them
together, and commit. A commit uses the staged Lock and needs a worktree
prepared for that Lock.

## Usage

```text
gateseal upgrade [COMPONENT[=VERSION] ...]
                 [--policy-tag TAG] [--policy URL | --policy bundled]
                 [--policy-path PATH] [--policy-commit COMMIT]
                 [--compiler-wheel PATH | --compiler-source URL] [--compiler-sha256 SHA256]
                 [--reason TEXT] [--review-by DATE] [--activate]
```

Put component names before flags. Use `--repo` to specify a worktree,
`--json` for a structured result, or `--verbose` for more detail. With no
target, `gateseal upgrade` reports the current selection.

## Targets

| What you want to change | What to select |
| --- | --- |
| Git policy | Use `--policy-tag` for a release in the current repository. To change the repository or directory, use `--policy` or `--policy-path`. Use `--policy-commit` to require an exact commit for the tag. |
| Bundled policy | Use `--policy bundled` for the policy that comes with the selected compiler. |
| Tool | Use `COMPONENT=VERSION` for an exact reviewed version. A version different from the policy pin needs a reason and review date. Selecting the pin explicitly removes the exception. |
| Compiler | Use the target wheel and digest from [Pin the release in a repository](install.md#pin-the-release-in-a-repository). The compiler also selects its runtime Python and pre-commit versions. |

A bare component name such as `betterleaks` normally follows the policy
pin. If the current version is newer and the target policy still reviews it,
gateseal keeps that version. If this creates an exception, supply a reason
and review date. To force a rollback to the pin, specify its exact version.

For a Git policy, gateseal checks the tag against the commit when it first
adopts the release. When the Lock already records the release, gateseal uses
the stored copy. See [policy acquisition](policy-acquisition.md).

Upgrade the compiler on its own or together with `--policy bundled`. Run
other policy or tool upgrades with the new compiler. To change the runtime
Python or pre-commit, select a compiler that carries the version you need.
To change the policy's [Hook Python](policy-format.md#hook-python), select a
policy release that pins it.

## Steps

gateseal checks that the Manifest matches the Lock, then checks the requested
versions and all existing exceptions against the target policy. If an
exception no longer fits, name the affected component in the same command
and select a reviewed version.

gateseal prepares the new Manifest, Lock, and generated files before writing
to the worktree. It preserves unrelated Manifest text. If the change would
overwrite an edited generated file, it stops with `ownership_conflict`.
It writes the Lock last and checks the result. With `--activate`, it also
prepares the worktree. You decide when to stage and commit the changes.

### An interrupted upgrade

If the Manifest still matches the old Lock, run the upgrade again. If the
new Manifest was written before the Lock, the next command can report
`lock_mismatch`. For a policy or component upgrade, run `gateseal configure`
to finish it.

For an interrupted compiler upgrade, review the diff first. Save any edits
you need to keep. Use `git restore` to restore `gateseal.toml` and the
affected generated files to their pre-upgrade contents. Then run the upgrade
again.

## Compiler targets

The current compiler checks the target wheel and uses uv to install it in a
temporary environment. The target compiler must be able to configure and
check your Manifest in a temporary Git repository. For a Git policy, the
target fetches the release and checks its tag. This temporary repository
inherits the worktree's applicable Git directory-based configuration.

If the new compiler ships a different bundled policy, the upgrade reports
`bundled_policy_moves`. Review that policy. To adopt it with the compiler, add
`--policy bundled`. A successful upgrade writes the files produced by the
target compiler. Other worktrees still need their own activation.

## Results

`gateseal upgrade --json` lists the changed paths. Each `upgrade` diagnostic
names a target and reports its state and its values before and after the
change. An `exception` diagnostic identifies the component, version, and
whether the exception was added, changed, removed, retained, or made
redundant. A redundant exception remains until you remove it.

| Result | Next step |
| --- | --- |
| `upgraded` or `unchanged` (exit 0) | Review any changed files. |
| `activation_failed` | The tracked files already contain the upgrade. Follow the activation error's next step. If the wheel cannot be downloaded, use the new command with `activate --compiler-wheel` and a matching copy. |
| `incompatible_exception`, `unknown_version`, `invalid_exception`, `unsupported_component`, `runtime_target` (exit 2) | Check the target policy, supported components, and exception fields. |
| `bundled_policy_moves` or `candidate_rejected` | Review the target compiler's message and the policy it accepts. |
| `candidate_unavailable`, `missing_artifact`, `artifact_hash_mismatch` (exit 4), or `invalid_artifact` (exit 2) | Check the wheel's location, filename, release, and digest. |
| `invalid_invocation`, `invalid_field`, `manifest_edit_unsupported`, `ownership_conflict`, `lock_mismatch` (exit 2) | Check the flags and the named files. Editable policy and exception sections need ordinary TOML tables. |

See [Manifest and Lock](policy-contract.md) for field definitions and
[upgrade tests](what-the-tests-prove.md#upgrades) for the cases covered.
