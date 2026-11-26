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
- **Local equals CI.** Commits and CI runs use the same tool versions, recorded in the Lock. A type checker such as Pyright or `tsc` reads the packages that the repository's lockfile pins.
- **Supply-chain security.** A moved tag can't change what runs. Hook repositories are pinned by commit ID, and release binaries and Node must match recorded checksums. A tool from npm installs a locked package tree with integrity hashes and runs no install script, [unlike pre-commit's Node hooks](typescript.md#gateseal-and-pre-commits-node-hooks).
- **Fast CI out of the box.** Pull requests check only changed files, and a manual or weekly run checks everything. Pyright checks the whole project each time, and the test checks run only when a Python file changed. A new push cancels the older run, and an optional time budget warns when the gate gets slow. [How it works](ci-gate.md#what-the-gate-checks).
- **Agent-friendly.** Commands give guidance without prompts. `--json` results say what failed and why, and the next step.

[Why gateseal](why-gateseal.md) compares it with pre-commit, pre-commit.ci, Dependabot, and Renovate.

## Start here

[Install gateseal](install.md). Then [configure a repository](configure-a-repository.md) with the hygiene and secrets checks:

```sh
gateseal configure --capability hygiene --capability secrets --ci github
```

In a Python repository, add `--capability python` for Ruff. In a TypeScript repository, add `--capability typescript` for ESLint and Prettier, as [Check TypeScript](typescript.md) shows.

- [Try the checks](demo.md) in a scratch repository.
- [Work in a configured repository](work-in-a-configured-repository.md) or [troubleshoot](troubleshooting.md).
- [Adjust checks](adjust-a-setup.md), enable CI on [GitHub](github-ci.md) or [Azure Pipelines](azure-pipelines.md), or [upgrade](upgrade.md).
- [Adopt a team policy](adopt-a-team-policy.md) or [author one](author-a-policy.md).
- Look up [commands](commands-and-results.md), [repository configuration](policy-contract.md), and [policy fields](policy-format.md).
- Read [how gateseal works](architecture.md).
