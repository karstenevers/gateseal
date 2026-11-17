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
| Staged hand edit of `.pre-commit-config.yaml` | The `gateseal-check` hook stops the commit and names `gateseal configure`. |
| Betterleaks program replaced after mise installed it | The `gateseal-check` hook stops a commit that stages a secret and names the program. With that hook skipped, the replaced program lets the secret through. After the install folder is deleted, the next commit installs Betterleaks again, and it blocks the secret. |
| A developer's own pre-commit on Python 3.11 or 3.12, and no gateseal on `PATH` | pre-commit builds the `gateseal-check` hook on that Python, and the commit passes. |
| Clone with `core.autocrlf=true` | Configure, validation, and a commit succeed without changes to tracked content. |

Tests also commit with a path, `-a`, `-i`, and a user-supplied alternate
index in a path that contains spaces. The hooks see the content that Git
plans to commit. A hook fix stops the commit, and retrying after staging the
correction succeeds.

## Adjusted setups

| Test condition | Observed result |
| --- | --- |
| A repository hook whose script refuses a file | A commit, `gateseal run`, and `gateseal gate` each run the script and stop. A hand edit of the hook in `.pre-commit-config.yaml` stops `gateseal check` and the commit. |
| A team policy marks `check-yaml` as optional, and the Manifest skips it | A file that `check-yaml` refuses passes a commit, `gateseal run`, and `gateseal gate`. Without the skip, all three stop. |
| A skip leaves Betterleaks without hooks | The repository has no Betterleaks file or Lock entry, and the CI gate runs no history scan of it. |
| A hook from Git's configuration and a `pre-commit.legacy` script | Both run before the checks of the repository at each commit. A hook fix still stops the commit, and `gateseal check` passes afterwards. |

## Worktrees

A newly linked worktree commits with the launcher of its clone, and keeps
doing so after `git worktree move`. Separate tests cover linked worktrees of
bare repositories, clones with `--separate-git-dir`, and a worktree whose
path contains shell characters.

## Upgrades

Tests upgrade and roll back both policy and gateseal releases. An exact
rollback restores the tracked bytes. A policy upgrade keeps choices and
compatible version exceptions. An incompatible exception or edited generated
output stops the upgrade before any write. Running the same upgrade twice
leaves the second run with nothing to change.

gateseal move tests install a second release and tag both in a local stand-in
for gateseal's repository. Two repositories move up and back on their own. A
move reads only the old Lock's ownership ledger, refuses to drop below the
policy's minimum gateseal, and moves the bundled policy only when asked. See
[Upgrade and roll back](upgrade.md).

See [Tests for policies and tools](what-the-tool-tests-prove.md) for Ruff and
Betterleaks behavior, and [Tested platforms](tested-platforms.md) for the
environments used.
