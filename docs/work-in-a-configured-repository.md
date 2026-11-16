# Work in a configured repository

If a repository contains `gateseal.toml` and `gateseal.lock`, its repository
maintainer has already selected the checks. Set up your machine once, then use
`git commit` as usual.

## Set up your machine

Install the gateseal version in the `gateseal.version` field of
`gateseal.lock`. For version `0.1.0`, run:

```sh
uv tool install --python 3.14.10 gateseal==0.1.0
gateseal setup
```

`gateseal setup` installs pre-commit and makes Git add pre-commit's commit
launcher to every new clone. [Set up pre-commit on this
machine](install.md#set-up-pre-commit-on-this-machine) says what it changes.
In a clone that you made before setup, run `gateseal setup --refresh` once.

A release binary, such as Betterleaks, starts through mise. Install mise
2026.10.19 or later if the repository selects one.
[Install gateseal](install.md) lists the tools you need first, and how to
install the commit that the Lock pins.

## Commit

Commits from your terminal or Git client check the files you stage. A commit
runs pre-commit, not gateseal.

The first commit that needs a tool downloads it, so it needs network access
and takes longer. pre-commit builds the environment of each hook repository.
mise installs each release binary and checks its SHA-256 against the value in
`.pre-commit-config.yaml`. Later commits reuse both, and all clones on the
machine share them. After you pull a changed Lock, the next commit installs
what changed.

To run the checks across every tracked file, use `gateseal run`. To see
whether the clone is ready, use `gateseal status`. If a gateseal command
reports `selected_compiler_required`, the installed gateseal is not the
release or build that the Lock pins.

## When a commit stops

- If a hook changes a file, review the fix with `git diff`, stage it, and commit again.
- If a hook reports a finding, fix the reported line and stage the change.
- If Betterleaks reports a secret, remove it. Rotate any real credential before you retry.
- If a hook cannot start, run `gateseal doctor` and follow its next steps.

You can stage part of an ordinary source file. Stage
`.pre-commit-config.yaml` in full, because pre-commit stops a commit while
that file has unstaged changes. On a branch without
`.pre-commit-config.yaml`, the launcher skips pre-commit and the commit goes
on. See [troubleshooting](troubleshooting.md) for other errors and recovery
steps.

## Linked worktrees

All worktrees of a clone share its commit launcher, so a worktree from
`git worktree add` needs no extra step. Clones with a separate Git directory
and worktrees attached to a bare repository work the same way.

A gateseal command in a worktree that uses a Git policy fetches the locked
policy release once for that worktree. A commit does not need it.
