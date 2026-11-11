# Troubleshoot gateseal

When gateseal stops a command or a commit, read its error and the `Next:`
instruction. If a hook stopped the commit, look for the filename and rule in
the hook's output.

## Inspect a worktree

From the affected worktree, check its preparation:

```sh
gateseal status --json
gateseal doctor --verbose
```

`status` reads local records. `doctor` checks the prepared runtime and tools
and reports their versions. If either command asks you to repair the
preparation, run `gateseal activate`.

## A commit or doctor names activation

Run `gateseal activate` in the affected worktree. It repairs missing or
damaged tools. If the preparation is already healthy, it keeps the installed
tools.

If activation stops partway through, the previous preparation stays selected.
On a first activation, Git starts using gateseal only after the hook is
installed. If the hook was installed before activation stopped, it blocks
commits until you finish activation.

## A command names a missing or old prerequisite

Install a supported version of the named tool and check that your shell
finds it first on `PATH`. Run the command again. If a commit reports an old
Git version, run `gateseal activate` after updating Git.

| Error (exit 4) | What to check |
| --- | --- |
| `missing_prerequisite` | The named executable is on `PATH`. |
| `git_version_mismatch` | `git --version` reports 2.54.0 or later. |
| `uv_version_mismatch` | `uv --version` reports 0.13.5 or later. Version 0.13.6 is tested. |
| `mise_version_mismatch` | `mise --version` reports 2026.10.19 or later. |

Activation needs mise for release binaries such as Betterleaks. See
[prerequisite floors](tested-platforms.md#prerequisite-floors).

## A commit names where the worktree was prepared

If you moved a worktree or copied a clone, run `gateseal activate` in its new
location. Preparation records the worktree it belongs to. Copies that followed
symbolic links may also need a new preparation.

## The Manifest was edited or the Lock is lost

After editing `gateseal.toml`, run `gateseal configure` to regenerate the
Lock and generated files. It keeps the existing compiler pin.

If `gateseal.lock` was deleted by mistake, restore it from Git. To create a
new Lock, [supply the wheel details](install.md#pin-the-release-in-a-repository)
again. Run `gateseal check` to confirm that the files agree, then
`gateseal activate` if the Lock changed.

## A hook changed a file

Run `git diff` to review the hook's edits. Stage the changes you want to
commit and try the commit again.

## Betterleaks stopped a commit

Remove the secret from the reported file. If it was a real credential,
rotate it. Stage the fix and try the commit again. For a false positive,
ask the policy author to review the rule. The bundled checks disable inline
allow comments and ignore files.

## gateseal reports an ownership conflict

If `configure` reports `ownership_conflict`, inspect the named file and save
any changes you need to keep. If the file is a generated output you edited,
restore its generated contents and make the intended change in the Manifest
or policy. If another tool owns the file, move it before you run
`gateseal configure` again.

If activation reports `unmanaged_hook`, it found an existing Git hook. Move
that hook out of the directory named in the message. Keep it for review,
then run `gateseal activate` again.

## Activation reports configured hooks

The `configured_hooks` error lists pre-commit hooks enabled through Git
configuration, including the scope where each is defined. Git runs these
before the gateseal hook. If you want gateseal to manage this repository's
commit checks, disable each listed hook for this repository. Replace
`HOOK_NAME` with the name in the error, then retry activation:

```sh
git config --local --replace-all hook.HOOK_NAME.enabled false
gateseal activate
```

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

Run `git rev-parse --git-path gateseal` to find the preparation directory.
Check the `patches` directory there. If the attempt that ran the commit
still exists, also check its `pre-commit-home`. Activation shows a
`git apply` command for each patch in `patches` until you delete it.
[Patches of unstaged edits](local-preparation.md#patches-of-unstaged-edits)
says when cleanup keeps or deletes a patch.

## Activation reports a hash mismatch for a Python archive

Delete the cached archive directory named in the error and run
`gateseal activate` again. This downloads a fresh copy. uv checks an
archive's SHA-256 on reuse and reports a mismatch if the cached copy is
damaged.

Wheel downloads use the uv cache, which you can set with `UV_CACHE_DIR`.
Python archives use `UV_PYTHON_CACHE_DIR` if set, or
`gateseal-python-archives` under the uv cache. Activation unpacks the
archive when its [interpreter store](interpreter-store.md) entry needs repair
or installation.

## Activation fails without network access

Restore network access and run `gateseal activate` again. For a first
activation with the bundled hygiene checks, the machine needs the locked
wheel, Python archive, pre-commit packages, and hygiene hook repository.
Python checks also need the Ruff hook repository and package. Secret checks
need the Betterleaks archive. Later activations reuse cached downloads.

If the compiler wheel is already on the machine, pass its path with
`gateseal activate --compiler-wheel /path/to/wheel`. For a team Git policy,
each worktree needs its own copy of the locked policy. See [which commands
fetch a policy](policy-acquisition.md#which-commands-fetch).

A prepared commit normally uses local tools. If a hook environment is
missing, pre-commit can try to download a dependency. See the
[cache-loss test](what-the-tests-prove.md#cache-loss) for this case.
