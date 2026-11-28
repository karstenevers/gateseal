<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/header-dark.svg">
    <img alt="gateseal, one check policy for every repository, locally and in CI" src="assets/header-light.svg" width="720">
  </picture>
</h1>

gateseal keeps one reviewed set of commit checks in every repository, on developer machines and in CI.

Platform teams review a policy release once. Each repository adopts it, and each commit and CI run checks that nothing drifted from it. [Why gateseal](why-gateseal.md) compares it with pre-commit, pre-commit.ci, Dependabot, and Renovate.

## Start here

[Install gateseal](install.md). Then [configure a repository](configure-a-repository.md) with the hygiene and secrets checks:

```sh
gateseal configure --capability hygiene --capability secrets --ci github
```

In a Python repository, add `--capability python` for Ruff. In a TypeScript repository, add `--capability typescript` for ESLint and Prettier.

To see a check work first, [try gateseal](demo.md) in a scratch repository. If your repository already uses gateseal, see [Work in a configured repository](work-in-a-configured-repository.md).
