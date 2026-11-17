# Troubleshoot gateseal

When a gateseal command stops, read its error and the `Next:` instruction.
If a hook stopped a commit, look for the filename and rule in the hook's
output.

Without gateseal installed, run each `gateseal` command below as
`uvx gateseal@VERSION`, with the version in the `gateseal.version` field of
`gateseal.lock`.

## Inspect a clone

From the affected worktree, run:

```sh
gateseal status --json
gateseal doctor --verbose
```

Both report [four checks](commands-and-results.md#reading-status-and-doctor):
the Lock, the pre-commit that `gateseal setup` installed, the commit launcher,
and mise. Follow the next step of each failed check. `doctor` also warns about
settings that keep a hook from running.

## A commit runs no checks

Run `gateseal doctor` in the clone.

- **The launcher check failed.** See
  [the clone has an old launcher](#the-clone-has-an-old-launcher).
- **`custom_hooks_path`.** See
  [Git's core.hooksPath is set](#gits-corehookspath-is-set).

## The clone has an old launcher

`gateseal doctor` fails its launcher check when the clone's
`.git/hooks/pre-commit` is not the launcher that setup installs. That happens
in a clone made before setup, or after another pre-commit or tool wrote the
hook. When the pre-commit that a launcher names was deleted, the launcher
starts the `pre-commit` on your `PATH`. Without one, each commit stops with
`` `pre-commit` not found ``.

Run `gateseal setup --refresh` in the clone. When the clone had a hook from
another tool, pre-commit keeps it as `.git/hooks/pre-commit.legacy` and runs it
first. Delete that file if you no longer need the hook.

## Git's core.hooksPath is set

Git then runs only the hooks in the folder that `core.hooksPath` names, so it
skips the clone's launcher. `gateseal setup` and `pre-commit install` both
refuse to run. Remove the setting, or put pre-commit's launcher in that folder,
as [When core.hooksPath is set](install.md#when-corehookspath-is-set) shows.

## Git LFS reports that a hook already exists

`gateseal setup` installs only the commit launcher. `git lfs install` then
adds its push and checkout hooks beside it, and a push uploads the LFS objects.
`git lfs install` stops with `Hook already exists: pre-push` when the clone has
another push hook, such as the one that `pre-commit install -t pre-push`
writes. To keep both, let Git LFS write its hook, then install pre-commit's
again:

```sh
git lfs update --force
pre-commit install -t pre-push
```

pre-commit keeps the Git LFS hook as `.git/hooks/pre-push.legacy` and runs it
first, so a push still uploads the LFS objects.

## A commit asks for a newer pre-commit

A commit stops with `pre-commit version X is required but version Y is
installed` when the repository moved to a gateseal release that names a newer
pre-commit. Run `uvx gateseal@VERSION setup` to install it, with the version
that the Lock pins. Without a version, uvx can run an older gateseal, whose
setup keeps the older pre-commit. If you installed pre-commit yourself,
upgrade it instead.

## A command reports needs_setup

`gateseal run` and `gateseal gate` start the pre-commit that `gateseal setup`
installed. Run `gateseal setup`, then run the command again.

## The first commit is slow or fails

The first commit that needs a tool downloads it, and later commits reuse it.
[How a commit runs](how-a-commit-runs.md#before-the-commit) lists what it
downloads.

- **No network access.** Restore network access and commit again.
- **A checksum does not match.** mise stops the hook when an archive does not
  have the SHA-256 in `.pre-commit-config.yaml`. Commit again. If the error
  repeats, tell the policy author, because the upstream file changed.
- **pre-commit cannot find `mise`.** Install mise 2026.10.19 or later and
  check that your shell finds it on `PATH`.

A commit does not fetch a team's Git policy. gateseal commands fetch it. See
[which commands fetch a policy](policy-acquisition.md#which-commands-fetch).

## A command names a missing or old prerequisite

Install a supported version of the named tool and check that your shell
finds it first on `PATH`. Run the command again.

| Error (exit 4) | What to check |
| --- | --- |
| `missing_prerequisite` | The named executable is on `PATH`. |
| `git_version_mismatch` | `git --version` reports 2.54.0 or later. |
| `uv_version_mismatch` | `uv --version` reports 0.13.5 or later. Version 0.13.6 is tested. |
| `mise_version_mismatch` | `mise --version` reports 2026.10.19 or later. |

A hook for a release binary, such as Betterleaks, starts through mise. See
[prerequisite floors](tested-platforms.md#prerequisite-floors).

## The Manifest was edited or the Lock is lost

After editing `gateseal.toml`, run `gateseal configure` to regenerate the
Lock and generated files. It keeps the existing gateseal pin.

If `gateseal.lock` was deleted by mistake, restore it from Git. Otherwise run
`gateseal configure`, which [pins the running gateseal](install.md#pin-the-release-in-a-repository)
again. Run `gateseal check` to confirm that the files agree.

## A hook changed a file

Run `git diff` to review the hook's edits. Stage the changes you want to
commit and try the commit again.

## Betterleaks stopped a commit

Remove the secret from the reported file. If it was a real credential,
rotate it. Stage the fix and try the commit again. For a false positive,
ask the policy author to review the rule. The bundled checks disable inline
allow comments and ignore files.

## gateseal reports an ownership conflict

If `configure` reports `ownership_conflict`, or the `gateseal-check` hook
stops a commit because a staged file differs from the file gateseal wrote,
inspect the named file and save any changes you need to keep. If the file is a generated output you edited,
restore its generated contents and make the intended change in the Manifest
or policy. If another tool owns the file, move it before you run
`gateseal configure` again.

## A commit reports a changed installed tool

If the `gateseal-check` hook stops a commit because an installed program,
such as `betterleaks`, is not the one `gateseal.lock` records, something
replaced the program after mise installed it. mise never checks an installed
tool again, so the replaced program would run in every commit. Find out what
changed it before you go on. Then delete the install folder that the message
names and commit again. The tool's hook has mise install it again from the
archive whose SHA-256 the Lock records.

## A command reports alternate_index

Repository commands expect the worktree's own Git index. If
`GIT_INDEX_FILE` points to another index, run `unset GIT_INDEX_FILE` and try
again. The `gateseal policy` commands operate on policy files and can run
with this variable set.

## Unstaged edits are missing after a stopped commit

pre-commit saves unstaged edits in a patch before checking the staged
files. If a commit stops before it restores those edits, the patch can hold
their only copy.

1. Find the line that starts with `[INFO] Stashing unstaged files to` in the
   commit output. It gives the path to the patch.
2. From the root of the worktree, run `git apply --whitespace=nowarn /path/to/patch`
   with that path.
3. Check `git diff` to confirm your edits are back before you delete the patch.

### When the commit's output is gone

Look in pre-commit's cache folder for files whose names start with `patch`.
The folder is `~/.cache/pre-commit`, unless `PRE_COMMIT_HOME` or
`XDG_CACHE_HOME` names another place.
