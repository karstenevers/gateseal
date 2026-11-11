<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/header-dark.svg">
    <img alt="gateseal, one check policy for every repository, locally and in CI" src="assets/header-light.svg" width="720">
  </picture>
</h1>

gateseal runs reviewed checks on the files you commit. Each repository records
the exact tool versions it uses. A generated GitHub Actions workflow can run
those same tools in CI.

## Get started

- [Try the demo](demo.md) to see a commit fail, fix the file, and commit again.
- [Prepare your clone](work-in-a-configured-repository.md) if the repository already uses gateseal.
- [Install gateseal](install.md) and [configure a repository](configure-a-repository.md) to start using the bundled checks.

## Manage checks

- [Run checks in GitHub Actions](github-ci.md) or [upgrade a tool, policy, or compiler](upgrade.md).
- [Adopt a team policy](adopt-a-team-policy.md), [write a policy](author-a-policy.md), or [add a tool](add-a-tool.md).

## Find an answer

- [Troubleshoot a failed check or activation](troubleshooting.md).
- [Look up a command or result](commands-and-results.md) or [find a term in the glossary](../GLOSSARY.md).
- [Follow a commit through its checks](how-a-commit-runs.md) or [read about the architecture](architecture.md).
- [See what the tests cover](what-the-tests-prove.md) or [build and test gateseal](verification.md).
