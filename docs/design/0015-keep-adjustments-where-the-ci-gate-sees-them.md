# Keep adjustments where the CI gate sees them

Accepted on 2026-11-16.

Repositories need to adjust the checks that a policy gives them. One wants a
check that is a script of its own, such as one that checks migration numbers.
Another wants to keep the hygiene checks but leave out `check-yaml`. A
developer wants a check of their own at each commit. Each of these can make a
commit run other checks than the CI gate. The gate then no longer repeats the
check that developers passed.

The rule is that a change that CI must run goes in the policy or the
Manifest, and the Lock records it. A change that only one developer wants
goes in Git and touches no file that gateseal writes. So a commit,
`gateseal run` and the CI gate keep running one generated configuration.

- **Repository Hooks.** A `[hooks.<id>]` table in the Manifest adds a hook
  that runs a script of the repository. It runs at every stage, because a
  hook that the gate skips would break the rule. It only adds a check: an ID
  or a name that a policy hook uses is refused, at configure and at a policy
  upgrade. gateseal installs nothing for it. A tool from outside the
  repository belongs in the policy, where its reviewed versions live.
- **Optional Hooks.** A policy marks hooks as optional, and a repository
  leaves them out with the choice `skip`. The policy exposes and the
  repository selects, as with alternatives and `exclude`. A tool that a skip
  leaves without hooks drops out of the configuration and the Lock. The CI
  gate then runs no history scan of it, so a commit and the gate agree. A
  policy can still make a secret scanner optional.
- **Personal Hooks.** A developer adds hooks in Git's configuration or in the
  clone's hooks folder. pre-commit's launcher runs a `pre-commit.legacy`
  script before the checks. Neither place is a file that gateseal owns, and
  neither can remove a check.

## Considered Options

- A Manifest table for scripts of the repository, generated into the
  configuration, optional hooks that the policy marks, and personal hooks in
  Git
- A free skip list in the Manifest. The policy release that a repository
  names would no longer say which checks run.
- A local override file, as other hook managers offer. Each lets a local run
  differ from CI, and gateseal would need a commit launcher of its own, which
  [design record 0013](0013-run-hooks-with-pre-commit-and-mise-and-keep-gateseal-a-compiler.md)
  removed.
- A repository hook limited to commits with `stages`. The CI gate would not
  run it.
- Refusing optional hooks on a tool with a history scan. A commit and the gate
  would agree too, but no policy could make a secret scanner optional.

## Consequences

- A Manifest without `[hooks]` compiles to the same bytes as before. The Lock
  needs no new field: the Manifest's SHA-256 covers the table, and the
  generated file's SHA-256 covers the hook.
- A policy upgrade that adds a hook with a repository hook's ID stops in its
  plan with `duplicate_identifier`, before it writes a file.
- A repository hook runs with the tools of the developer's machine and the CI
  runner. One that needs the project's own dependencies in CI is not covered.
- `skip` is a name that gateseal reserves in every capability, as `exclude`
  is.
- `gateseal status` and `gateseal doctor` report nothing about personal
  hooks.
