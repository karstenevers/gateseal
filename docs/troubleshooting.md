# Troubleshoot gateseal

When a gateseal command stops, read its error and the `Next:` instruction.
If a hook stopped a commit, look for the filename and rule in the hook's
output.

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

- **The launcher check failed.** The clone has no commit launcher, or another
  hook is in its place. Run `gateseal setup --refresh`. A hook that the clone
  already has stays as `.git/hooks/pre-commit.legacy`, and the launcher runs
  it first.
- **`custom_hooks_path`.** Git's `core.hooksPath` is set, so Git skips the
  launcher. Run `git config --show-origin core.hooksPath` to find the file
  that sets it, and remove the setting.

## A command reports needs_setup

`gateseal run` and `gateseal gate` start the pre-commit that `gateseal setup`
installed. Run `gateseal setup`, then run the command again.

## The first commit is slow or fails

The first commit that needs a tool downloads it. pre-commit clones each hook
repository and builds its environment, and mise downloads each release
binary. Later commits reuse them.

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
