# Work in a configured repository

If a repository contains `gateseal.toml` and `gateseal.lock`, its repository
maintainer has already selected the checks. Set up pre-commit on your machine
once, then use `git commit` as usual. You do not install gateseal.

## Set up your machine

Run setup once per machine, with uv 0.13.6 or later. Name the gateseal
version in the `gateseal.version` field of `gateseal.lock`:

```sh
uvx gateseal@0.2.0 setup
```

It installs pre-commit and makes Git add pre-commit's commit launcher to every
new clone. [Set up pre-commit on this
machine](install.md#set-up-pre-commit-on-this-machine) says what it changes.
In a clone that you made before setup, run
`uvx gateseal@0.2.0 setup --refresh` once.
If you already have pre-commit 4.7.0 or later, run `pre-commit install` in each
clone instead. That is enough for commits.
[Use pre-commit without gateseal setup](install.md#use-pre-commit-without-gateseal-setup)
shows how.

A release binary, such as Betterleaks, starts through mise. Install mise
2026.10.19 or later if the repository selects one.

## Commit

Commits from your terminal or Git client check the files you stage. A commit
runs pre-commit, and pre-commit runs the gateseal that the Lock pins as its
first hook.

The first commit that needs a tool downloads it, so it needs network access
and takes longer. pre-commit builds the environment of each hook repository,
gateseal's own among them.
mise installs each release binary and checks its SHA-256 against the value in
`.pre-commit-config.yaml`. Later commits reuse both, and all clones on the
machine share them. After you pull a changed Lock, the next commit installs
what changed.

To run the checks across every tracked file, use `gateseal run`. To see
whether the clone is ready, use `gateseal status`. Both need the pre-commit
that setup installs, and the gateseal version that the Lock pins, for example
`uvx gateseal@0.2.0 run`. With another version, `gateseal run` stops with
`selected_compiler_required`, and `gateseal status` reports a failed Lock
check. Both name the version that the Lock pins. Without setup,
`pre-commit run --all-files` runs the checks across every tracked file.

## When a commit stops

- If a hook changes a file, review the fix with `git diff`, stage it, and commit again.
- If a hook reports a finding, fix the reported line and stage the change.
- If Betterleaks reports a secret, remove it. Rotate any real credential before you retry.
- If `gateseal-check` reports that a staged file differs from the file gateseal
  wrote, undo the edit. To change a check, change `gateseal.toml` and run
  `gateseal configure` instead.
- If `gateseal-check` reports that an installed program is not the one
  `gateseal.lock` records, find out what changed it. Then delete the folder
  that the message names and commit again. See
  [troubleshooting](troubleshooting.md#a-commit-reports-a-changed-installed-tool).
- If a hook cannot start, run `gateseal doctor` and follow its next steps.

You can stage part of an ordinary source file. Stage
`.pre-commit-config.yaml` in full, because pre-commit stops a commit while
that file has unstaged changes. On a branch without
`.pre-commit-config.yaml`, the launcher that `gateseal setup` adds skips
pre-commit and the commit goes on. One from plain `pre-commit install` stops
the commit. See [troubleshooting](troubleshooting.md) for other errors and recovery
steps.

## Add your own hooks

You can add hooks that run only on your machine. Git runs them at your
commits, and no file in the repository changes. CI and `gateseal run` do not
run them, and they cannot turn off a check of the repository. For a check
that everyone runs, ask the repository maintainer for a
[repository hook](policy-contract.md#repository-hooks).

**In Git's configuration.** Git runs these hooks before pre-commit's
launcher. These lines run a script of yours at each commit in this clone.
Add `--global` to run it in every clone.

```sh
git config hook.notes.event pre-commit
git config hook.notes.command "$HOME/bin/check-notes"
```

**In the clone's hooks folder.** The launcher runs a `pre-commit.legacy`
script there before the checks of the repository. A script named for another
event, such as `pre-push`, runs at that event. A slow check fits `pre-push`
better than a commit. Make the script executable:

```sh
chmod +x "$(git rev-parse --git-path hooks)/pre-commit.legacy"
```

Do not add a hook to `.pre-commit-config.yaml`. The `gateseal-check` hook
stops a commit that stages an edit of it, and pre-commit stops while that
file has unstaged changes.

## Linked worktrees

All worktrees of a clone share its commit launcher, so a worktree from
`git worktree add` needs no extra step. Clones with a separate Git directory
and worktrees attached to a bare repository work the same way.

A gateseal command in a worktree that uses a Git policy fetches the locked
policy release once for that worktree. A commit does not need it.
