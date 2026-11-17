# Adjust a setup

Each change to the checks has one place. A change that CI must run goes in the
policy or in `gateseal.toml`, and `gateseal.lock` records it. A change for one
developer goes in Git and touches no file that gateseal writes. So a commit
and the CI gate always run the same checks.

| Change | Who makes it | Where |
| --- | --- | --- |
| Change a rule or a tool version for every repository | Policy Author | [A new policy release](author-a-policy.md) |
| Add a tool | Policy Author | [A new policy release](add-a-tool.md) |
| Select the checks for a repository | Repository Maintainer | [`capabilities`](policy-contract.md#capabilities) in `gateseal.toml` |
| Set a choice, such as a formatter or a target version | Repository Maintainer | [`[choices.<capability>]`](policy-contract.md#choices) |
| Keep paths out of a capability's checks | Repository Maintainer | [`exclude`](policy-format.md#exclude) in `[choices.<capability>]` |
| Leave out a hook that the policy marks as optional | Repository Maintainer | [`skip`](policy-contract.md#skip-optional-hooks) in `[choices.<capability>]` |
| Keep a tool on another reviewed version | Repository Maintainer | [`[exceptions.<tool>]`](policy-contract.md#version-exceptions) |
| Run a script of the repository as a check | Repository Maintainer | [`[hooks.<id>]`](policy-contract.md#repository-hooks) |
| Add a check for yourself only | Developer | [Git configuration or the clone's hooks folder](work-in-a-configured-repository.md#add-your-own-hooks) |

After an edit of `gateseal.toml`, run `gateseal configure`. Commit the
Manifest, the Lock and the generated files together.

## What you cannot change

- **A generated file.** `gateseal check`, the `gateseal-check` hook and the CI
  gate compare `.pre-commit-config.yaml` and the files under `.gateseal/` with
  the SHA-256 that the Lock records. A hand edit stops all three. Change
  `gateseal.toml` or the policy instead.
- **A hook that the policy does not mark as optional.** A repository skips
  only what its policy offers, so the policy release still says which checks
  run. Ask the Policy Author to mark the hook as optional in a new release.
- **A check of your own in CI.** CI runs only the checks that the Lock
  records. To make CI run a script, add it as a repository hook.
