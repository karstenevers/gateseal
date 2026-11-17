# Troubleshoot gateseal

Read the error and its `Next:` instruction. For a stopped commit, also read the hook's filename and rule.

From the affected worktree, run:

```sh
gateseal doctor --verbose
```

The report checks the selected configuration, setup's pre-commit, the commit launcher, and mise. Follow the reported next step. See [Commands and results](commands-and-results.md#status-and-doctor).

## A commit runs no checks

Run `pre-commit install` from the clone root. If setup installed pre-commit, `gateseal setup --refresh` also installs its launcher in the clone. pre-commit preserves an existing hook as `pre-commit.legacy` and runs it first.

## Git uses a different hooks directory

A `core.hooksPath` setting makes Git use that directory instead of the clone's hooks. Remove the setting and run `pre-commit install` in each clone. Alternatively, install the launcher in that directory. Preserve any existing `pre-commit` there as `pre-commit.legacy` first. To create a launcher, run `pre-commit init-templatedir -t pre-commit` with a temporary directory. Copy its `hooks/pre-commit` into the configured directory.

## A command reports needs_setup

Run `gateseal setup`. Then retry. `gateseal run` and `gate` need the pre-commit that setup installs. If a commit asks for a newer pre-commit, update your separate installation. Alternatively, run setup with the release recorded in `gateseal.lock`. The Lock records the selected policy and generated files.

## The first commit fails

The first commit downloads hook environments and selected binaries. Check network access. Then retry. A release binary such as Betterleaks needs mise 2026.10.19 or later on `PATH`. If an archive checksum keeps failing, ask the policy author to investigate the upstream release. Each clone uses the same selected versions.

## A generated file differs

`gateseal.toml` is the Manifest that selects the policy and checks. To change checks, edit the Manifest. Run `gateseal configure`. Review the Manifest, Lock, and generated files. Commit them together. If the Lock was deleted by mistake, restore it from Git. If `ownership_conflict` names an edited generated file, save any changes you need. Restore the generated file before retrying.

## A hook changes a file or reports a secret

Review the edits with `git diff`. Stage the intended changes. Retry the commit. For a real credential, remove it. Rotate it. For a false positive, ask the policy author to review the rule. The author can turn the rule off or skip the path in the policy's rule file. The bundled Betterleaks checks ignore repository ignore files and inline allow comments. Neither can suppress a finding.

## A commit reports a changed installed tool

Investigate what replaced the program. Remove the installation directory named in the message. Then retry the commit. mise installs the recorded archive again and checks its SHA-256.

## A command reports alternate_index

Unset `GIT_INDEX_FILE`. Retry the repository command. Policy authoring commands can run with that variable set.

## Unstaged edits are missing after a stopped commit

pre-commit saves unstaged edits in a patch before checking staged files. The patch can hold the only copy if restoration stopped. Find its path in the `[INFO] Stashing unstaged files to` line. From the worktree root, replace `/path/to/patch` with that path. Run:

```sh
git apply --whitespace=nowarn /path/to/patch
git diff
```

Confirm that the edits are back before deleting the patch. If the output is gone, look for files whose names start with `patch` in `~/.cache/pre-commit`. `PRE_COMMIT_HOME` or `XDG_CACHE_HOME` can select another cache.
