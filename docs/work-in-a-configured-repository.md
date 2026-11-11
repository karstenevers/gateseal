# Work in a configured repository

If a repository contains `gateseal.toml` and `gateseal.lock`, its repository
maintainer has already selected the checks. Prepare your clone once, then use
`git commit` as usual.

## Install the pinned release

Find `compiler.version` in `gateseal.lock`. [Install that
release](install.md#install-from-pypi). If a command reports
`selected_compiler_required`, check `gateseal --version` against the Lock.
For a team build, get the exact wheel that the Lock names from the
repository maintainer.

## Prepare the clone

From the root of your clone, run:

```sh
gateseal activate
```

Activation prepares the checks in this worktree and installs the Git hook.
It uses the versions in `gateseal.lock`, not the tools installed on your
machine.
The first activation needs network access to download the tools. If the
locked wheel is at a path or URL you cannot reach, use
`gateseal activate --compiler-wheel /path/to/wheel`. Supply a copy with the
filename and SHA-256 recorded in the Lock.

After activation, commits from your terminal or Git client check the files
you stage. Run `gateseal activate` again after you pull a changed Lock. If
the compiler version changed, install the new pinned release first.
To run the checks across every tracked file, use `gateseal run`.

## When a commit stops

- If a hook changes a file, review the fix with `git diff`, stage it, and commit again.
- If a hook reports a finding, fix the reported line and stage the change.
- If Betterleaks reports a secret, remove it. Rotate any real credential before you retry.
- If gateseal stops the commit, read the first line and the `Next:` instruction.

You can stage part of an ordinary source file. Stage each generated file in
full, including `.pre-commit-config.yaml` and files under `.gateseal`.
If a branch lacks the gateseal configuration, merge or rebase a branch that
contains it. To deliberately skip Git's commit checks on that branch, use
`git commit --no-verify`. See [troubleshooting](troubleshooting.md) for other
errors and recovery steps.

## Prepare each worktree

After `git worktree add`, run `gateseal activate` in the new worktree. All
worktrees in the clone share a Git hook, but each needs its own preparation.
A worktree using a Git policy also fetches the locked policy release. Until
activation succeeds, the hook stops commits in the new worktree.

Create linked worktrees with Git. Copying a linked worktree's folder can
leave it pointing to the original Git directory. Clones with a separate Git
directory and worktrees attached to a bare repository also support activation.
To remove a linked worktree and its preparation, use `git worktree remove`.

Prepared worktrees on the same machine share read-only copies of Python and
hook environments. See the [interpreter store](interpreter-store.md) for its
location, settings, and cleanup.
