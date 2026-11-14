# Run hooks with pre-commit and mise, and keep gateseal a compiler

Accepted on 2026-11-14 and not implemented yet. It replaces design records
[0003](0003-prepare-gateseal-tools-before-commits.md),
[0010](0010-share-one-sealed-interpreter-store-across-repositories.md) and
[0011](0011-share-sealed-hook-environments-through-the-interpreter-store.md).
It also replaces parts of records [0001](0001-delegate-native-checks-provisioning-to-mise.md),
[0004](0004-acquire-git-policy-releases-by-exact-commit.md),
[0006](0006-generate-policy-lock-data-and-ship-the-runtime-with-the-compiler.md),
[0007](0007-implement-tool-kinds-in-the-compiler-and-describe-every-tool-as-data.md),
[0008](0008-generate-the-ci-gate-as-an-owned-workflow.md) and
[0009](0009-upgrade-through-one-planner-and-let-the-candidate-render-a-compiler-move.md).
Each of them names the part.

Under design record 0003, gateseal prepares every tool for each worktree in
advance, and a commit runs only what that preparation holds. In gateseal 0.1.0,
this local runtime is about a third of the code and of the tests. It does work
that pre-commit and mise already do, and each new platform would need a port of
it.

So gateseal keeps one job. It compiles a Manifest and a Policy into a Lock and
owned files, and it checks that nobody changed them. Other tools do the rest.

## What gateseal leaves to other tools

- **pre-commit starts the hooks.** `gateseal setup` runs once per machine. It
  installs the pre-commit version that gateseal names, with uv, on a Python that
  gateseal supports. It writes pre-commit's commit launcher into a Git template
  folder, so Git copies the launcher into every new clone. pre-commit builds
  each hook environment once per machine and owns it.
- **mise supplies the binaries.** Each generated hook starts its binary through
  `mise exec`, from the mise file that gateseal writes. mise checks each
  download's checksum and keeps one install of each version per machine.
- **CI is the gate.** The generated workflow runs the same hooks and the full
  check on a fresh runner. A developer can skip a local hook with
  `git commit --no-verify`, but a change cannot skip a required check.

Python hooks run on the Python that pre-commit runs on. A Policy no longer pins
a Hook Python.

`gateseal setup` installs no push launcher. With a push launcher in the Git
template, `git lfs install` fails, and a push then uploads no Git LFS object
without an error. A repository adds a push hook when its first tool needs one.

## What gateseal keeps

gateseal keeps the Manifest, the Policy, the Lock, the owned files and the
check that nobody changed them. gateseal itself runs as one more pre-commit
hook.

- The Lock records gateseal's repository, its version and the commit of its
  release tag.
- The generated configuration uses that commit as the `rev` of gateseal's own
  hook repository, as it does for every other hook repository.
- At `git commit`, the `gateseal-check` hook compares the staged Manifest, Lock
  and owned files with the Lock. It needs no network and no Policy.
- For a tool that its Tool Definition marks, such as the secret scanner
  Betterleaks, the hook also checks the installed binary against a checksum
  that the repository records.
- CI installs gateseal at the same commit with uv.

To move to a newer gateseal, a Repository Maintainer installs it and runs
`gateseal upgrade`. That gateseal writes its own version and commit into the
Lock.

From this record on, releases share only the Lock's ownership ledger: the Lock
format, the gateseal pin, the Manifest's SHA-256, and each owned file's path
and SHA-256. Every other field belongs to the release that wrote it.

- The hook and CI run the gateseal that the Lock pins, so they read only a Lock
  that their own release wrote.
- `configure` and `check` refuse a Lock that pins another gateseal. The message
  names that gateseal and `gateseal upgrade`.
- `gateseal upgrade` accepts another release's Lock. It compiles from the
  Manifest and the Policy alone, and it reads the old ledger only to decide
  which files it may replace or delete. As under design record 0009, a gateseal
  move never moves the Policy or a tool to fit.
- The Lock format changes only when the ledger changes. gateseal refuses a
  format that it does not know. It also knows the Lock format of gateseal
  0.1.0, so a move from 0.1.0 is an ordinary `gateseal upgrade`.

Between releases that follow this record, a rollback is `git revert` of the
upgrade commit. The upgrade writes only tracked files, and the hook and CI take
gateseal from the Lock's commit. So the revert restores everything, needs no
install, and works across a format change. When later commits make the revert
conflict, an older gateseal moves the repository back with `gateseal upgrade`
while the Lock format is the same. Across a format change, it refuses and names
`git revert`.

gateseal 0.1.0 predates this contract. Reverting a move from 0.1.0 brings back
its wheel pin and its activation, so every developer installs 0.1.0 and runs
`gateseal activate` again. 0.1.0 refuses a Lock that another release wrote, so
it cannot move a repository back with `gateseal upgrade`.

## What a developer gives up

- **An offline first commit.** The first commit in a new clone downloads what
  the machine does not have yet. In a test on Linux, three first commits took
  11 s, 22 s and 259 s. The cause of the slow run is not known. Later commits
  took 0.3 to 0.4 s.
- **Proof of every tool's version at every commit.** mise checks a download,
  but it never checks an installed tool again. In the same test, a replaced
  binary ran without a warning. The check hook covers the tools that their Tool
  Definitions mark, and CI covers the rest.
- **A hard stop on `core.hooksPath`.** When a tool such as husky sets
  `core.hooksPath`, Git skips the launcher without a message. `gateseal doctor`
  warns about it, and CI still runs the gate.
- **A clean environment for Python hooks.** `mise exec --deny-env` gives a
  binary only `HOME`, `LC_CTYPE`, `PATH`, `SHELL` and `TERM`. pre-commit starts
  a Python hook with the developer's environment, so a variable such as
  `RUFF_OUTPUT_FILE` reaches Ruff.

pre-commit and CI build gateseal from source at its commit. The build backend
comes from PyPI by its exact version, without a checksum. pre-commit installs
every Python hook repository this way, Ruff's included.

## Considered options

- Keep the local runtime. It gave an offline commit and a version proof for
  every tool. It cost about a third of the code and of the tests, and two
  formal models.
- Keep both designs behind a switch. Both would need their own tests, and the
  cost of the old design would stay.
- Pin gateseal by the URL and SHA-256 of its wheel. A repository's first
  `gateseal configure` and each move to a newer gateseal would then need a
  checksum. gateseal would also install itself in a way that no other hook uses.
- Let a release read all of another release's Lock within one format. Every
  field would then bind every later release, while the ledger alone is enough
  for an upgrade.
- Copy a generated check script into every repository. A hook pinned by commit
  needs no copied code and moves like every other hook.
- Set a global `core.hooksPath`. Git would then ignore the hooks in each
  repository's `.git/hooks`, such as the one that Git LFS installs.

## Consequences

- A developer runs `gateseal setup` once per machine, not `gateseal activate`
  in every worktree. Every new clone then runs its hooks at `git commit`.
- Git never overwrites a hook that a clone already has. `gateseal setup
  --refresh` writes the launcher into an existing clone.
- The launcher reaches every new clone on the machine. In a repository without
  a pre-commit configuration, it does nothing.
- `gateseal configure` needs no checksum.
- The Prepared Attempt, the Interpreter Store, the Compiler Runtime and the
  Candidate Compiler go away, with their formal models.
