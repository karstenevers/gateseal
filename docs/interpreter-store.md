# Interpreter store

Activation shares Python interpreters and pre-commit hook environments across
repositories on one machine. Each prepared attempt links to the entries it
uses. Only activation installs, repairs, or removes entries. Commits read the
entries directly.

## Location

On both supported platforms, activation uses the first applicable path.

1. The absolute path in `GATESEAL_INTERPRETER_STORE`, if set.
2. `$XDG_DATA_HOME/gateseal/interpreters`, if `XDG_DATA_HOME` is absolute.
3. `~/.local/share/gateseal/interpreters`.

A relative `GATESEAL_INTERPRETER_STORE` fails with `unsupported_path`.
The resolved store path has the same character restrictions as the
[worktree's Git directory](local-preparation.md#refused-paths).

## Interpreter entries

An interpreter entry takes its name from the SHA-256 of its Python archive.
Two repositories that need the same archive share the installed interpreter.
A policy can also request a separate [Hook Python](policy-format.md#hook-python).

| Path in the store | Purpose |
| --- | --- |
| `<sha256>/` | uv's install directory for the archive. The interpreter uses this absolute path. |
| `<sha256>.json` | The *seal* records the archive key and digests of file metadata and contents. Activation writes it after the entry is complete. |
| `<sha256>.refs/` | Links to the attempts that use the entry. |
| `store.lock` | Serializes installations and repairs across repositories. |

Activation checks the seal and both digests. If an entry is missing or
damaged, uv installs the archive at its original path. Activation sets fixed
modification times, makes the files read-only, and writes a new seal. A
successful repair restores the identity that existing attempts recorded.

Commits do not wait for a repair. If a commit encounters an interpreter error
during a repair in another repository, retry after activation finishes.

## Hook environments

The store also holds the clone and environment that pre-commit builds for
each selected hook repository. Two attempts share a hook entry when these
inputs match:

- Platform and pre-commit runtime packages.
- Python interpreter, including the policy's Hook Python if selected.
- Hook repository, revision, and selected hook settings that affect its environment.

Each attempt still has its own `pre-commit-home`. Its database points to the
shared environments. Patches that pre-commit writes while saving unstaged edits
stay with the attempt. See [Local preparation](local-preparation.md#patches-of-unstaged-edits).

### After a hook environment changes

Rebuilding a hook environment can change its paths and modification times.
Other worktrees that recorded the old entry then stop at the next check.
Run `gateseal activate` in each affected worktree to use the rebuilt entry.

pre-commit can also try to rebuild an environment if it loses an interpreter
after the commit's initial checks. A later activation detects and repairs the
changed entry. See [Cache loss](what-the-tests-prove.md#cache-loss).

## Collection

During cleanup, activation removes links to missing attempts. It removes an
entry and its seal when the last link is gone. If another activation holds
the store lock, collection waits for a later activation.

An unmounted volume can make a worktree's attempt look missing. After you
mount the volume, run `gateseal activate` in that worktree to restore its
links. Activate a copied clone in its new location for the same reason.

## App firewalls

On macOS, an app firewall can ask for network access when Python installs
a hook's packages. The interpreter path is shared across worktrees that use
the same entry. A rule for that path can cover all of them. A new CPython
archive or a different Hook Python uses a new path.

In a prepared worktree, print the interpreter path with this command.

```sh
readlink "$(git rev-parse --git-path gateseal)/current/hook/python"
```

If the policy pins a separate Hook Python, its `python<version>` link under
`current/tools/` points to the interpreter that installs the hook packages.
