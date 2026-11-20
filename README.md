<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/header-dark.svg">
    <img alt="gateseal, one check policy for every repository, locally and in CI" src="docs/assets/header-light.svg" width="720">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/karstenevers/gateseal/actions?query=branch%3Amain"><img alt="Status of every CI check on main" src="https://img.shields.io/github/check-runs/karstenevers/gateseal/main?label=CI&logo=github"></a>
  <a href="https://gateseal.readthedocs.io/"><img alt="Documentation build status" src="https://img.shields.io/readthedocs/gateseal?logo=readthedocs&logoColor=white&label=docs"></a>
  <a href="docs/install.md#requirements"><img alt="platforms macOS arm64 and Linux x86-64" src="https://img.shields.io/badge/platforms-macOS%20arm64%20%7C%20Linux%20x86--64-3e6fa8"></a>
  <a href="docs/install.md#requirements"><img alt="requires Git 2.54 and uv 0.13.6 or later" src="https://img.shields.io/badge/requires-Git%202.54%2B%20%7C%20uv%200.13.6%2B-3e6fa8"></a>
</p>


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
- **Fast CI out of the box.** Pull requests check only changed files, and a manual or weekly run checks everything. A check of the whole project, such as Pyright, runs each time. A new push cancels the older run, and an optional time budget warns when the gate gets slow. [How it works](docs/ci-gate.md#what-the-gate-checks).
- **Agent-friendly.** Commands give guidance without prompts. `--json` results say what failed and why, and the next step.

## When not to use it

For one or two repos, a pre-commit config and a CI job that runs `pre-commit run --all-files` cover most needs. Use gateseal when you also want drift checks, pinned binaries, or one standard across many repos.

A team standard needs a policy repository plus a Manifest and a Lock in each repository. The bundled policy needs no policy repository.

## Get started

Install Git 2.54.0 or later, pre-commit 4.7.0 or later, [mise](https://mise.jdx.dev/) 2026.10.19 or later, and [uv](https://docs.astral.sh/uv/). From a Git repository, run:

```sh
uv tool install gateseal
gateseal configure --capability hygiene --capability secrets --ci github
pre-commit install
```

This turns on file hygiene checks, Betterleaks for secrets, and a GitHub Actions workflow. In a Python repository, add `--capability python` for Ruff. In a TypeScript repository, add `--capability typescript` for [ESLint and Prettier](docs/typescript.md).

Review the generated files and commit them together. `gateseal.toml`, the Manifest, selects the policy and checks. `gateseal.lock`, the Lock, records that selection and the generated files. See [Install gateseal](docs/install.md) for requirements and [Configure a repository](docs/configure-a-repository.md) for each step.

Teammates need Git and pre-commit, but not gateseal. They also need mise for release binaries such as Betterleaks and for tools from npm such as ESLint, and uv for tools that run in the uv project, such as Pyright. See [Work in a configured repository](docs/work-in-a-configured-repository.md).

## What it supports

- **Bundled checks:** file hygiene, Ruff and Pyright for Python, ESLint, Prettier and tsc for TypeScript, and Betterleaks for secrets. A team policy can [add other tools](docs/add-a-tool.md).
- **CI:** GitHub Actions and Azure Pipelines (Azure DevOps).
- **Platforms:** macOS on arm64 and Linux on x86-64.
- **Planned:** upgrade pull requests for new tool releases, and Windows.

## Documentation

- [Try gateseal](docs/demo.md) in a scratch repository.
- [Adjust checks](docs/adjust-a-setup.md), enable CI on [GitHub](docs/github-ci.md) or [Azure Pipelines](docs/azure-pipelines.md), or [upgrade](docs/upgrade.md).
- [Adopt a team policy](docs/adopt-a-team-policy.md) or [author one](docs/author-a-policy.md).
- [Troubleshoot](docs/troubleshooting.md), look up [commands](docs/commands-and-results.md), or read [how gateseal works](docs/architecture.md).
- [Contribute](CONTRIBUTING.md) or report a vulnerability as [SECURITY.md](SECURITY.md) describes.

[MIT license](LICENSE)
