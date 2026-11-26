# Troubleshoot gateseal

Read the error and its `Next:` instruction. For a stopped commit, also read the hook's filename and rule.

From the affected worktree, run:

```sh
gateseal status --verbose
```

The report checks the selected configuration, setup's pre-commit, the commit and push launchers, and mise. Follow the reported next step. See [Commands and results](commands-and-results.md#status).

## A commit runs no checks

Run `pre-commit install` from the clone root. If setup installed pre-commit, `gateseal setup --refresh` also installs its launcher in the clone. pre-commit preserves an existing hook as `pre-commit.legacy` and runs it first.

## A push runs no checks or stops for Git LFS

Run `pre-commit install` from the clone root. It installs the push hook if the repository has checks at a push. If setup installed pre-commit, run `gateseal setup --refresh` instead. It also adds the hook that starts Git LFS at each push. If a push reports that no push hook starts Git LFS, see [Git LFS](work-in-a-configured-repository.md#git-lfs).

In a repository with SHA-256 object names, pre-commit 4.7.0 stops the deletion of a remote branch. Add `--no-verify` to that `git push`.

## Git uses a different hooks directory

A `core.hooksPath` setting makes Git use that directory instead of the clone's hooks. Remove the setting and run `pre-commit install` in each clone. Alternatively, install the launcher in that directory. Preserve any existing `pre-commit` there as `pre-commit.legacy` first. To create a launcher, run `pre-commit init-templatedir -t pre-commit` with a temporary directory. Copy its `hooks/pre-commit` into the configured directory.

If the repository has checks at a push, install the push hook the same way. Preserve any existing `pre-push` there as `pre-push.legacy` first. It is often the push hook of Git LFS. Run `pre-commit init-templatedir -t pre-commit -t pre-push` instead, and copy `hooks/pre-push` too.

## A command reports needs_setup

Run `gateseal setup`. Then retry. `gateseal run` and `gate` need the pre-commit that setup installs. If a commit asks for a newer pre-commit, update your separate installation. Alternatively, run setup with the release recorded in `gateseal.lock`. The Lock records the selected policy and generated files.

## A command reports download_failed

`gateseal fetch` or `gateseal gate` could not download a release binary or build a hook environment. No check ran. Run the command again, or start the CI job again. If the download fails each time, read the output of mise or pre-commit after the first line. See [A failed download](ci-gate.md#a-failed-download).

## The first commit fails


The first commit downloads hook environments and selected binaries. Check network access. Then retry. To make these downloads before a commit, run `gateseal fetch`. A release binary such as Betterleaks needs mise 2026.10.19 or later on `PATH`, and so does a tool from npm such as ESLint. Such a tool downloads Node from nodejs.org and its packages from the npm registry, through your proxy, CA and registry settings. If its hook says `npm cannot install the locked packages`, read npm's error above that line. `EINTEGRITY`, or `npm installed no` and a package, means that the registry sent a package whose hash differs from the one that `policy.lock` records, or sent none, so retry, and ask the policy author to investigate if it keeps failing. `The lockfile is not the one that the hook names` means that a `.gateseal/<tool>.package-lock.json` changed, so run `gateseal configure`. If its hook says that mise has no install of the pinned Node, turn mise's auto-install on. Pyright needs uv on `PATH` and a current `uv.lock`. Its first run installs the project's packages. If an archive checksum keeps failing, ask the policy author to investigate the upstream release. Each clone uses the same selected versions.

## A type checker does not read the project's packages

A tool that runs in the uv project reads its own environment, `.gateseal/environments/<name>`, not `.venv`. uv installs into it each package's dependencies and the default dependency groups. If the checked code imports from an extra or another group, ask the policy author to list it in the tool's definition. A policy that copies the bundled Pyright rules for a tool with another name sets `venv` to that name.

## A command reports shadowed_tool

`uv.lock` installs a package with a tool's name and pinned version from the project's own source, such as a path or a Git repository. uv would run that package in place of the reviewed release. Install the package from a package index, or give the project's own package another version. Then run `uv lock`.

## The test hook does not find pytest

The `tests with coverage` hook runs pytest in the repository's uv project. Put pytest in a dependency group that uv installs by default, such as `dev`, or in a group that the tool's definition lists.

The hook, and a team's tool that imports the project's packages itself, also need a uv cache whose path holds no space. With a space, uv starts the program outside the project, so it does not find pytest or the project's packages. Run `uv cache dir`. If the path holds a space, set `UV_CACHE_DIR` to a path without one. Then retry.

## A push stops at the Node project's packages

The hook `gateseal-node-packages` installs the packages that the lockfile pins before `tsc` runs. If `package-lock.json` or `pnpm-lock.yaml` does not match `package.json`, run `npm install` or `pnpm install`. Commit the lockfile. Then push again. The checks after the install run only when it passes. If the project lacks `package.json` or a lockfile, the hook names the file to add.

If `tsc` reports missing types after the install, check whether an install script or a code generator makes them. No install script runs. Commit the generated files. If it reports `error TS5101` or `error TS5102`, `tsconfig.json` sets an option that TypeScript 7 removed, such as `baseUrl`. See [Which TypeScript runs](typescript.md#which-typescript-runs).

## A setting of npm or pnpm stops a push

The hook `gateseal-node-packages` obeys the settings of the project and the user, in `.npmrc`, `pnpm-workspace.yaml` and the environment, as `npm install` or `pnpm install` in a terminal does. A setting can stop the push, but it cannot make the push pass with other packages than the lockfile pins. Run `npm config list` or `pnpm config list` in the repository to see the settings.

- `tsc` reports `error TS2307: Cannot find module`: a setting leaves out a package that `tsc` needs, such as `omit=dev` or `production=true`. Remove the setting, or move the package into `dependencies`.
- pnpm reports `ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY`: `node_modules` holds an install with other settings, such as one from `pnpm install --prod`. pnpm asks before it removes `node_modules`, and a push has no terminal for the question. Run `pnpm install` in a terminal and answer yes.

Then push again.

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
