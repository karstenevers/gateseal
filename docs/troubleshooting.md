# Troubleshoot gateseal

Read the error and its `Next:` instruction. For a stopped commit, also read the hook's filename and rule.

From the affected worktree, run:

```sh
gateseal doctor --verbose
```

The report checks the selected configuration, setup's pre-commit, the commit and push launchers, and mise. Follow the reported next step. See [Commands and results](commands-and-results.md#status-and-doctor).

## A commit runs no checks

Run `pre-commit install` from the clone root. If setup installed pre-commit, `gateseal setup --refresh` also installs its launcher in the clone. pre-commit preserves an existing hook as `pre-commit.legacy` and runs it first.

## A push runs no checks or stops for Git LFS

Run `pre-commit install` from the clone root. It installs the push hook if the repository has checks at a push. If a push reports that no push hook starts Git LFS, see [Git LFS](work-in-a-configured-repository.md#git-lfs).

In a repository with SHA-256 object names, pre-commit 4.7.0 stops the deletion of a remote branch. Add `--no-verify` to that `git push`.

## Git uses a different hooks directory

A `core.hooksPath` setting makes Git use that directory instead of the clone's hooks. Remove the setting and run `pre-commit install` in each clone. Alternatively, install the launcher in that directory. Preserve any existing `pre-commit` there as `pre-commit.legacy` first. To create a launcher, run `pre-commit init-templatedir -t pre-commit` with a temporary directory. Copy its `hooks/pre-commit` into the configured directory.

If the repository has checks at a push, install the push hook the same way. Preserve any existing `pre-push` there as `pre-push.legacy` first. It is often the push hook of Git LFS. Run `pre-commit init-templatedir -t pre-commit -t pre-push` instead, and copy `hooks/pre-push` too.

## A command reports needs_setup

Run `gateseal setup`. Then retry. `gateseal run` and `gate` need the pre-commit that setup installs. If a commit asks for a newer pre-commit, update your separate installation. Alternatively, run setup with the release recorded in `gateseal.lock`. The Lock records the selected policy and generated files.

## The first commit fails

The first commit downloads hook environments and selected binaries. Check network access. Then retry. A release binary such as Betterleaks needs mise 2026.10.19 or later on `PATH`. Pyright needs uv on `PATH` and a current `uv.lock`. Its first run installs the project's packages. If an archive checksum keeps failing, ask the policy author to investigate the upstream release. Each clone uses the same selected versions.

## A type checker does not read the project's packages

The bundled Pyright reads `.venv` at the repository root, where uv installs the project's packages. `UV_PROJECT_ENVIRONMENT` makes uv install them in another folder. Pyright then reads an outdated `.venv`. Without a `.venv`, it prints that it did not find one and reads the packages of the Python that starts it, which finds Pyright's own dependencies first. Unset the variable for commits, or ask the policy author to name the other folder in the Pyright rules.

If a commit passes and the CI gate reports a missing import, `.venv` holds a package that the hook does not install. Run `uv sync`. Then retry. The hook installs each package's dependencies and its default dependency groups. For a package from an extra or another group, add the group to `tool.uv.default-groups` in `pyproject.toml`, or name the extra in the `dev` group.

A team's tool that imports the project's packages itself needs a uv cache whose path holds no space. Run `uv cache dir`. If the path holds a space, set `UV_CACHE_DIR` to a path without one. Then retry.

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
