# Local preparation

Each worktree has its own prepared tools and records. To find them, run this
command in that worktree.

```sh
git rev-parse --git-path gateseal
```

In an ordinary clone, the path is `.git/gateseal`. In a linked worktree,
it is under `worktrees/<name>/` in the main repository's Git directory.
`gateseal activate` maintains these files. You do not commit them.

## Layout

| Path in the preparation directory | Contents |
| --- | --- |
| `current` | Relative link to the worktree's current prepared attempt. Activation replaces this link after preparation succeeds. |
| `attempt-<32 hexadecimal digits>/` | Tools and records for one preparation of the Lock. |
| `attempts.lock` | Coordinates commits, runs, and cleanup. |
| `policies/` | Git policy releases that this worktree has acquired. |
| `patches/` | Unstaged edits that cleanup retained from old attempts. |

| Path inside an attempt | Contents |
| --- | --- |
| `prepared.json` | Preparation record, written last. |
| `compiler/`, `pre-commit/` | Separate environments for the compiler and pre-commit. The corresponding `.txt` files hold their hashed requirements. |
| `mise/` | Local mise state and installed release binaries. |
| `pre-commit-home/` | Database pointing to the shared hook environments, and patches of unstaged edits. |
| `hook/python`, `hook/bootstrap.py` | Links to the interpreter and the selected compiler's bootstrap. |
| `tools/` | First directory on the hooks' `PATH`. It holds links for selected native tools, such as `gateseal-betterleaks`, and the separate Hook Python when selected. |

### The record

`prepared.json` binds the worktree location, platform, and Lock to the
prepared files. Its schema-1 data includes the following fields.

| Field | Contents |
| --- | --- |
| `replaces` | Previous attempt, or null. |
| `paths.tools`, `outputs` | Tools directory and owned output paths in Lock order. |
| `native_tools`, `hook_python` | Versions and executable paths for native tools and any separate Hook Python. |
| `trees` | Metadata and content digests of the compiler, pre-commit, native tools, interpreter entries, and hook environments. |
| `hook_environments` | Each hook environment's component, Python, installation marker digest, and required executables. |

The record belongs to the worktree that created it. Activate a moved or
copied worktree in its new location.

## The installed hook

The worktrees of a repository share one `pre-commit` script in the common
Git directory's `hooks/` directory. gateseal recognizes its own script by
its bytes and refuses to replace another hook. When Git starts the script,
it finds `current` for the worktree making the commit. It passes Git's
`GIT_DIR` and `GIT_INDEX_FILE` through when Git sets them.

### Refused paths

The resolved Git directory and interpreter store paths can contain spaces
and non-ASCII characters. Activation rejects paths containing `=`, a quote,
a dollar sign, a backtick, a backslash, or a colon. These characters can
break the hook's environment, the Python launchers, or the hooks' `PATH`.
A linked worktree's name can also appear in its Git directory path.

### Hooks that Git runs instead or first

A custom `core.hooksPath` directs Git to another hook directory. If set after
activation, it can bypass the gateseal hook. Git can also run enabled
pre-commit hooks from its configuration before the hook file. Activation
refuses both settings. `gateseal status`, `doctor`, and `run` report them.
Require the [CI gate](github-ci.md#the-gate) if pushed commits must pass checks.

## Staged Lock selection

A commit reads its Manifest, Lock, and generated files from the index Git
selected. The checks run in this order.

| Condition | Result |
| --- | --- |
| Staged Manifest or Lock is missing, conflicted, unreadable, or invalid | Stop and identify the file to fix. |
| Staged Lock differs from the prepared Lock, and the working Manifest and Lock match the staged files | Stop and request `gateseal activate`. Activation can prepare the staged selection. |
| Staged Lock differs from the prepared Lock, and the working Manifest or Lock differs from the staged file | Stop and identify the file to stage or restore. Activation would prepare a different selection. |
| A staged generated file is missing, invalid, or differs from its working copy | Stop and identify the file to fix, stage, or restore. |
| All checks pass | Run the prepared compiler against the staged configuration. |

## Cleanup

A running commit, `gateseal run`, or `gateseal gate` holds a shared lock on
the attempt. Cleanup needs exclusive access. If the lock is busy, cleanup
leaves the files for a later activation. It keeps the current attempt and its
immediate predecessor. It checks older attempts for patches before removal.

### Patches of unstaged edits

When pre-commit saves unstaged edits, it writes a patch in the attempt's
`pre-commit-home`. Cleanup compares the patch's expected result with the
worktree, including file modes and deletions. It can discard a restored
patch after at least an hour, allowing an interrupted checkout time to finish.

If the worktree differs, cleanup moves the patch to `patches/`. A later
edit can cause this even if pre-commit restored the original change.
Activation reports the saved path. Inspect the patch and your working files
before using the suggested `git apply` command. Activation continues to
report the patch until you remove it.

## Prepared attempt invariants

The following guarantees describe concurrent activation and commits.

1. Only activation publishes an attempt, by replacing `current` with a link
   to an attempt that has a record.
2. Once a commit passes the preparation checks, it uses one attempt until
   it exits. Cleanup preserves that attempt.
3. Cleanup retains saved unstaged edits until the worktree matches the
   patch result and the patch is at least an hour old.
4. Activation installs the hook before publishing. If it stops between
   those steps, the hook checks for a complete attempt.
5. A failed or interrupted preparation keeps the previous attempt current.
6. The staged Lock must match the pinned attempt, and staged generated files
   must match their working copies before pre-commit starts.
7. Activation preserves tracked files.
8. A commit requests activation for a different staged Lock only when the
   working Manifest and Lock match the staged files.
9. Recorded attempts keep references to their sealed interpreter entries.
   If another worktree rebuilds a shared hook environment, activate this
   worktree again. An unmounted volume can also require reactivation.

If two activations publish while a commit starts, cleanup can remove the
resolved attempt before the commit obtains its lock. That commit stops
before running checks. The next commit can use the current attempt.
See [Interpreter store](interpreter-store.md) for shared entries and repair.

A [TLA+ model](../formal/PreparedAttempts.tla) checks invariants 1 to 4 and
9, and the case of two publishes. It tries every order of steps within its
bounds.
Invariant 5 holds because a publish replaces `current` last. Tests of
the installed command cover invariants 6 to 8.
[Model-check the prepared attempts](verification.md#model-check-the-prepared-attempts)
shows how to run the model.

## Integrity checks

A commit, `status`, and `run` compare paths, modes, sizes, and modification
times against the record. They also check expected executables and tool
links. `doctor` and activation additionally compare file contents by hash
and run health probes. For example, they check the prepared Ruff version and
run lint and formatting probes on empty input with the owned configuration.

A matching record cannot establish that every third-party hook dependency
will still run. See [Cache loss](what-the-tests-prove.md#cache-loss) and
[What gateseal trusts](architecture.md#what-gateseal-trusts).
