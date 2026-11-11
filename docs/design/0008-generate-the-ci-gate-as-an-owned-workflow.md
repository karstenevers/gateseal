# Generate the CI gate as an owned workflow

Accepted on 2026-11-01 and implemented for GitHub Actions. It applies [design record 0003](0003-prepare-gateseal-tools-before-commits.md)'s rule that disposable CI jobs prepare deliberately, and it keeps room for per-tool stages.

A repository's CI has to run the Lock that its commits run, on a runner that has nothing prepared. A common answer is a generated workflow that installs pinned tools and plans each event's scope in inline shell and Python. gateseal keeps that event planning but moves it out of the workflow.

So a Manifest's `[ci]` table makes `.github/workflows/gateseal.yml` an owned output that configure writes and the Lock hashes. The workflow embeds the compiler wheel's URL and SHA-256, the Compiler Runtime's CPython, and checksummed uv and mise downloads, so a compiler move shows in its diff. A policy move shows in the Lock's diff; the workflow names only the policy repository that its credential helper serves. Its preparation step installs the locked compiler and runs `gateseal activate`, the same preparation a contributor runs, so CI gets no second acquisition path. Activation runs the prepared compiler's `check`, which refuses a Manifest, Lock, or owned output that disagrees, the workflow itself included.

The gate is a command, `gateseal gate`, that takes the event's revisions as flags. The compiler plans the scope from the clone alone. A change is checked from its merge base, a fast-forward push from `before`, and a new branch, a rewrite, or a missing `before` on every file. A deleted ref has nothing to check. The workflow keeps only a `case` that maps GitHub's event fields to those flags, so the planning is typed, unit-tested Python, and another CI provider reuses it.

The gate selects its hooks through pre-commit's own stages. The CI stage is pre-commit's `manual` stage, and every built-in hook runs in every stage, so the gate runs the hooks a commit runs. A later tool that runs only in CI or only at commit sets its stages in its Tool Definition and needs no workflow change.

A secret that one commit adds and a later commit removes is gone from the change's last tree but stays in its history. A Tool Definition of a kind whose hooks run a prepared executable may therefore declare a history scan: arguments that hold a `{range}` placeholder, where the gate puts the planned revision range. Betterleaks declares `git ... --log-opts=--diff-merges=first-parent {range}`, so the gate reads every commit of a change, and also what a merge commit adds.

pre-commit's revision flags select file names but read the files from the worktree, so a gate that names a head requires that commit checked out without uncommitted changes. GitHub runs a pull request's workflow from its merge commit, which already holds any compiler or policy move on the base branch, so the gate checks that merge commit too, and the workflow, the Lock it prepares, and the files it checks agree. The history range then ends at the merge commit, whose first-parent diff repeats the pull request's net change, so a secret that is still there is reported once more.

The policy token lives only in the preparation step, and Git receives it only for a policy repository on `https://github.com/`, through a credential helper scoped to its URL. uv downloads the compiler wheel without credentials. The workflow has read-only permissions, no path filters, and a checkout without persisted credentials.

## Considered options

- Plan events in the workflow. Inline Python in YAML is hard to test and would be copied into every provider's template.
- Read the compiler from `gateseal.lock` at run time. The workflow would then not change when the compiler moves, but a reviewer would no longer see in its diff what CI installs, and the runner would parse the Lock before any gateseal code validated it.
- Run the history scan as a CI-only pre-commit hook. A hook receives file names, not a revision range, and the issue keeps every built-in hook in every stage.
- Name the gate's hooks in the workflow. Each new tool would then need a workflow change, which the stage selection avoids.
