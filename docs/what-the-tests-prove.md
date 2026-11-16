# What the tests prove

The integration suite drives the installed gateseal command in Git clones
and linked worktrees. Each test machine has a home of its own, where
`gateseal setup` ran. The suite runs real hooks with Git, uv, pre-commit,
mise, and the selected check tools. These cases show what to expect when a
commit or an upgrade goes wrong.

## Setup and commits

| Test condition | Observed result |
| --- | --- |
| Fresh clone on a machine where `gateseal setup` ran | A plain `git commit` runs the hooks. No gateseal command runs in the clone. |
| Hygiene hook fixes a file | The commit stops. After the test stages the fix, the next commit passes. |
| Minimal GUI-style `PATH` | The launcher still runs the pre-commit that setup installed. |
| Clone without a launcher, or with `core.hooksPath` set | `gateseal doctor` names the failed launcher check, or warns about the setting, each with its next step. |
| Partially staged source file | The commit checks the staged part and keeps the unstaged edits. |
| Clone with `core.autocrlf=true` | Configure, validation, and a commit succeed without changes to tracked content. |

Tests also commit with a path, `-a`, `-i`, and a user-supplied alternate
index in a path that contains spaces. The hooks see the content that Git
plans to commit. A hook fix stops the commit, and retrying after staging the
correction succeeds.

## Worktrees

A newly linked worktree commits with the launcher of its clone, and keeps
doing so after `git worktree move`. Separate tests cover linked worktrees of
bare repositories, clones with `--separate-git-dir`, and a worktree whose
path contains shell characters.

## Upgrades

Tests upgrade and roll back both policy and compiler releases. An exact
rollback restores the tracked bytes. A policy upgrade keeps choices and
compatible version exceptions. An incompatible exception or edited generated
output stops the upgrade before any write. Running the same upgrade twice
leaves the second run with nothing to change.

Compiler upgrade tests use a second wheel. A missing or corrupt wheel, or a
candidate compiler that cannot accept the selected policy, leaves the
tracked files in place. See [Upgrade and roll back](upgrade.md).

See [Tests for policies and tools](what-the-tool-tests-prove.md) for Ruff and
Betterleaks behavior, and [Tested platforms](tested-platforms.md) for the
environments used.
