<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/header-dark.svg">
    <img alt="gateseal, one check policy for every repository, locally and in CI" src="docs/assets/header-light.svg" width="720">
  </picture>
</p>

<h3 align="center">Pin your checks like you pin your dependencies.</h3>

<p align="center">
  <a href="https://github.com/karstenevers/gateseal/actions?query=branch%3Amain"><img alt="Status of every CI check on main" src="https://img.shields.io/github/check-runs/karstenevers/gateseal/main?label=CI&logo=github"></a>
  <a href="https://gateseal.readthedocs.io/"><img alt="Documentation build status" src="https://img.shields.io/readthedocs/gateseal?logo=readthedocs&logoColor=white&label=docs"></a>
  <a href="docs/tested-platforms.md"><img alt="platforms macOS arm64 and Linux x86-64" src="https://img.shields.io/badge/platforms-macOS%20arm64%20%7C%20Linux%20x86--64-3e6fa8"></a>
  <a href="docs/tested-platforms.md#prerequisite-floors"><img alt="requires Git 2.54 and uv 0.13.6 or later" src="https://img.shields.io/badge/requires-Git%202.54%2B%20%7C%20uv%200.13.6%2B-3e6fa8"></a>
</p>

<p align="center">
  <a href="#install"><b>Install</b></a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="docs/demo.md">Demo</a> ·
  <a href="#documentation">Documentation</a>
</p>

## Why gateseal

pre-commit runs the hooks. gateseal adds what a team needs around them.

- **One policy for every repository.** Tools, versions, rules, and CI inputs
  come from one reviewed release. No more copying configuration files into
  each repository and updating them by hand.
- **Exact versions.** Your commits and CI use the tool versions recorded in
  `gateseal.lock`. gateseal installs those versions itself and ignores the
  tools installed on your machine. Two repositories on the same machine can
  use different versions of a tool, such as Betterleaks 1.9.0 and 1.10.0,
  without conflict.
- **Fewer CI failures.** gateseal generates the CI workflow from the same Lock.
  A formatting error or a secret stops the commit on your machine, so fewer CI
  runs fail and you wait less for CI.

## Who gateseal is for

**Developers:** run `gateseal activate` in each worktree, and every commit
checks the staged files with the locked tools that activation installed.

**Repository owners:** pin one policy release in `gateseal.lock`, and plain
`gateseal` shows whether the repository matches it.

**Platform teams:** review a policy release once and adopt it in every
repository through a reviewed pull request.

**Coding agents:** get non-interactive commands whose `--json` results name
what failed and what to run next.

## What it supports

### Built-in checks

Built-in checks ship in the bundled policy, so a repository needs no policy of
its own to use them. It turns each one on by its capability, such as
`--capability hygiene`.

- Supported:
  - `hygiene`: trailing whitespace, file endings, YAML
  - `python`: Ruff
  - `secrets`: Betterleaks
- Planned:
  - Semgrep
  - SQL
  - Infrastructure security
  - Dependency audits

### Tools you can add

Your own tools go in a team policy. It can add a tool of any supported kind
without a new gateseal release, as [Add a tool](docs/add-a-tool.md) shows.

- Supported:
  - Python pre-commit hook repositories
  - Release binaries on GitHub
- Planned:
  - Node packages, such as Prettier and ESLint
  - Release binaries from other hosts, such as Terraform, or as single files
  - Tools that import the project, such as mypy
  - Tested examples for shell, Dockerfile, GitHub Actions, spelling and YAML tools

### CI

- Supported: GitHub Actions
- Planned: Azure Pipelines

## Install

gateseal runs on macOS on arm64 and on Linux on x86-64 with glibc. Native
Windows is not supported yet, and support is planned. The generated GitHub
Actions workflow runs on GitHub's Linux runner `ubuntu-24.04`. You cannot
choose another runner yet.

It needs these tools:

- Git 2.54.0 or later
- uv 0.13.6 or later
- mise 2026.10.19 or later, for the secrets check only

```sh
uv tool install --python 3.14.10 gateseal
gateseal --version
```

[Install gateseal](docs/install.md) explains the Python flag, installs the
release that a repository pins, and installs a wheel by its reviewed SHA-256.

## Quick start

Adopt the bundled policy in a repository. `hygiene` is required. Leave out
`python` or `secrets` if you do not want them, and `--ci github` if you do not
use GitHub Actions:

```sh
gateseal configure \
  --capability hygiene \
  --capability python \
  --capability secrets \
  --compiler-source IMMUTABLE_WHEEL_URL \
  --compiler-sha256 REVIEWED_SHA256 \
  --ci github
gateseal activate
```

Review and commit `gateseal.toml`, `gateseal.lock`, `.pre-commit-config.yaml`,
`.github/workflows/gateseal.yml`, and, with `python` or `secrets`, the
generated `.gateseal` directory.

From then on, every `git commit` in that worktree runs the checks that the Lock
pins. GitHub Actions runs the same checks on each pull request and each push to
`main`. A teammate who clones the repository runs `gateseal activate`, and runs
it again after pulling a new Lock.

When a commit stops, its last lines say why. A hook that fixed a file leaves
the fix in the worktree, so review it, stage it, and commit again. When
gateseal and not a hook stops a commit, its first line names the problem and
its `Next:` line the fix.
[Configure a repository](docs/configure-a-repository.md) explains each
capability, and [GitHub CI](docs/github-ci.md) explains the workflow.

## How it fits together

**Manifest:** `gateseal.toml` selects a reviewed policy, its capabilities, and
its choices. `configure` compiles it into the Lock, `gateseal.lock`, and a
native `.pre-commit-config.yaml`, which you review and commit.

**Activation:** prepares one worktree with the pinned CPython, pre-commit, hook
repositories, and tools. It writes nothing that Git tracks. Every worktree and
repository on the machine shares one copy of Python and of each hook
environment.

**Hook:** runs only what activation prepared, on what each commit stages. When
part of it is missing, the commit stops and names the step that fixes it.

**CI and upgrades:** CI runs the same Lock in a generated GitHub Actions
workflow, and `upgrade` moves the policy, a tool, or the gateseal release
forward or back.

A policy is reviewed data that pins every tool to an exact version, by commit
or SHA-256. gateseal bundles one, and a team can publish its own as a tagged
directory in a Git repository. [Architecture](docs/architecture.md) shows who
owns each part, what gateseal trusts, the key decisions, and how the tests
prove it works.

## Documentation

| Goal | Start here |
| --- | --- |
| Work in a repository that uses gateseal | [Work in a configured repository](docs/work-in-a-configured-repository.md) · [Troubleshooting](docs/troubleshooting.md) |
| Set up a repository | [Configure a repository](docs/configure-a-repository.md) · [GitHub CI](docs/github-ci.md) · [Upgrade and roll back](docs/upgrade.md) |
| See it work | [Demo](docs/demo.md) |
| Use a team's policy | [Adopt a team policy](docs/adopt-a-team-policy.md) · [Authenticate to a private policy](docs/authenticate.md) |
| Write a team policy | [Author a team policy](docs/author-a-policy.md) · [Add a tool](docs/add-a-tool.md) · [Policy format](docs/policy-format.md) |
| Understand how it works | [How a commit runs](docs/how-a-commit-runs.md) · [Architecture](docs/architecture.md) · [Vision](VISION.md) |
| Look up commands and files | [Commands and results](docs/commands-and-results.md) · [Manifest and Lock](docs/policy-contract.md) · [Local preparation](docs/local-preparation.md) · [Glossary](GLOSSARY.md) |
| See how gateseal is tested | [What the tests prove](docs/what-the-tests-prove.md) · [Build and verify](docs/verification.md) |

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) says which changes fit and how to run the
checks. Report a vulnerability as [SECURITY.md](SECURITY.md) says.

## License

[MIT](LICENSE)
