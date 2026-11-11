# What the tests prove

The integration suite drives the installed gateseal command in Git clones
and linked worktrees. It runs real hooks with Git, uv, pre-commit, and the
selected check tools. These cases show what to expect when a commit, an
upgrade, or a local installation goes wrong.

## Preparation and commits

| Test condition | Observed result |
| --- | --- |
| Wrong Python archive or compiler wheel digest | Installation stops. A damaged runtime or an unexpected module stops a commit before the prepared compiler starts. |
| Missing, conflicted, malformed, or unsupported staged Lock | The hook stops before the compiler starts and reports the next step. |
| Interrupted activation | The previous attempt stays current if one exists. A retry that cannot download Python leaves it alone. |
| Hygiene hook fixes a file | The commit stops. After the test stages the fix, the next commit passes. |
| Minimal GUI-style `PATH`, or uv removed after activation | The installed hook still runs the prepared tools. |
| Edited working copy of the generated configuration | The commit stops. Ordinary partially staged source keeps its unstaged edits. |
| Clone with `core.autocrlf=true` | Configure, activation, validation, and a commit succeed without changes to tracked content. |

Tests also commit with a path, `-a`, `-i`, and a user-supplied alternate
index in a path that contains spaces. The hooks see the content that Git
plans to commit. A hook fix stops the commit, and retrying after staging the
correction succeeds.

## Worktrees

A newly linked worktree needs its own activation. The original worktree and
the linked one can then commit with different compiler releases. Moving a
worktree requires activation in the new location. Removing the worktree
leaves the original preparation usable. Separate tests cover linked worktrees
of bare repositories and clones with `--separate-git-dir`.

A test also commits with the working Lock from a different release. The
staged Lock determines which prepared compiler runs. The hook requests
activation only if activation would prepare the staged selection.

## Upgrades

Tests upgrade and roll back both policy and compiler releases. An exact
rollback restores the tracked bytes. A policy upgrade keeps choices and
compatible version exceptions. An incompatible exception or edited generated
output stops the upgrade before any write. Running the same upgrade twice
leaves the second run with nothing to change.

Compiler upgrade tests use a second wheel. A missing or corrupt wheel, or a
candidate compiler that cannot accept the selected policy, leaves the
tracked files in place. If activation fails *after* a valid upgrade, the
tracked upgrade remains available to review and activate. See
[Upgrade and roll back](upgrade.md).

## Cache loss

One test removes a prepared hook environment. `gateseal doctor` and the Git
hook both request activation. Running pre-commit directly then makes it try
to install a dependency. With `PIP_NO_INDEX=1`, this install fails.
Reactivation repairs the shared entry, and a real commit passes with the
same tracked Lock.

Another test reuses a cached Python archive with uv offline. A recompressed
archive with the same contents fails uv's hash check. These experiments
cover specific caches. They do not block network access system-wide.
See [Activation fails without network access](troubleshooting.md#activation-fails-without-network-access).

## Interpreter store

- Two repositories that activate concurrently install one copy of the same
  Python archive. Both can then commit using it.
- Activation repairs a missing or damaged interpreter entry, including a
  file whose content changed while its size and timestamp stayed the same.
  After repair, other repositories that used the same interpreter can commit.
- Two clones and a linked worktree share a read-only hook environment. If an
  activation rebuilds it, another worktree that recorded its old identity
  requests activation before the next commit.
- Collection removes unreachable entries once no existing attempt refers to
  them. A busy store lock defers collection to a later activation.

See [Tests for policies and tools](what-the-tool-tests-prove.md) for Ruff and
Betterleaks behavior, and [Tested platforms](tested-platforms.md) for the
environments used.
