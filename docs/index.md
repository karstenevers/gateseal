<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/header-dark.svg">
    <img alt="gateseal, one check policy for every repository, locally and in CI" src="assets/header-light.svg" width="720">
  </picture>
</h1>

gateseal keeps one reviewed set of commit checks in every repository, on developer machines and in CI.

## Who it is for

- **Platform teams** review a policy release once and roll it out to many repositories.
- **Repository maintainers** adopt a release and see when the repository drifts from it.
- **Developers and coding agents** commit as usual. pre-commit runs the pinned checks.

## Why use it

- **One source of truth.** A versioned policy records tool versions, rules, binary checksums, and CI settings.
- **Drift detection.** Each commit and CI run compares the generated files with the Lock.
- **Local equals CI.** Commits and CI runs use the same tool versions, recorded in the Lock.
- **Supply-chain security.** A moved tag can't change what runs. Hook repositories are pinned by commit ID, and release binaries must match recorded checksums.
- **Fast CI out of the box.** The generated workflow checks only what a pull request changed. With the secrets checks on, Betterleaks scans its commits. A scheduled run checks every file. You do not have to maintain this logic. [How it works](github-ci.md#what-the-gate-checks).
- **Agent-friendly.** Commands give guidance without prompts. `--json` results say what failed and why, and the next step.

## Start here

[Install gateseal](install.md). Then [configure a repository](configure-a-repository.md) with the hygiene and secrets checks:

```sh
gateseal configure --capability hygiene --capability secrets --ci github
```

In a Python repository, add `--capability python` for Ruff.

- [Try the checks](demo.md) in a scratch repository.
- [Work in a configured repository](work-in-a-configured-repository.md) or [troubleshoot](troubleshooting.md).
- [Adjust checks](adjust-a-setup.md), [enable CI](github-ci.md), or [upgrade](upgrade.md).
- [Adopt a team policy](adopt-a-team-policy.md) or [author one](author-a-policy.md).
- Look up [commands](commands-and-results.md), [repository configuration](policy-contract.md), and [policy fields](policy-format.md).
- Read [how gateseal works](architecture.md).
