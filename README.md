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


gateseal sets up the same checks for every repository, on developer machines and in CI, as easily as adding a dependency. You name the checks, and a lock file pins every tool version.

- **Platform teams** review a policy release once.
- **Repositories** adopt it. Each commit and CI run checks that nothing drifted from it.
- **Developers and coding agents** commit as usual.

## Quick start

With uv, Git 2.54+ and pre-commit 4.7+ installed, run this from the root of a Git repository:

```sh
uv tool install gateseal                             # install gateseal
gateseal configure --capability hygiene --ci github  # write the Manifest, the Lock and a CI workflow
pre-commit install                                   # check each commit
```

Review the generated files and commit them together. To add checks later, list more capabilities in `gateseal.toml`, the Manifest, and run `gateseal configure` again:

```toml
# gateseal.toml, the Manifest
schema = 1
capabilities = ["hygiene", "secrets", "python"]

[policy]
source = "bundled"
release = "2026.11-team-tools"

[ci]
provider = "github"
default_branch = "main"
```

Some checks need more tools, such as mise for Betterleaks. [Configure a repository](docs/configure-a-repository.md) shows each step.

## Why gateseal

- **One reviewed source.** A versioned policy records tool versions, rules, checksums and CI settings.
- **Local equals CI.** Commits, pushes and CI runs use the same pinned versions. Each commit and CI run fails when a generated file drifts from the Lock.
- **Safe installs.** A moved tag cannot change what runs. Hook repositories are pinned by commit ID, and release binaries and Node must match recorded checksums. A tool from npm installs locked packages with integrity hashes and runs no install script.
- **Fast CI.** Most checks see only the changed files of a pull request, and a new push cancels the older run.
- **Agent-friendly.** Commands never prompt. `--json` results say what failed and the next step.

For one or two repositories, a pre-commit config and a CI job can be enough. [Why gateseal](docs/why-gateseal.md) compares it with pre-commit, pre-commit.ci, Dependabot and Renovate.

## What it supports

### Built-in checks

You turn on checks in groups, by capability name. A capability you do not select adds nothing. Any repository can select file hygiene with pre-commit-hooks and secret scans with Betterleaks.

| Check | Python | TypeScript |
| --- | --- | --- |
| Lint and format | Ruff | ESLint, Prettier |
| Types | Pyright | `tsc`, type-aware ESLint rules |
| Dependencies | `uv lock --check`, deptry | knip |
| Tests with coverage | pytest with coverage.py | Vitest or Jest |
| CRAP score of each function in a changed file | From the coverage.py report | From the Vitest or Jest report |
| Mutation tests of the changed lines | pytestmut | StrykerJS |
| Known vulnerabilities, in CI | `uv audit` | OSV-Scanner, for npm |

Some checks run at each commit and others at each push. Tests run only when a push changes a source file. The CI gate runs the same checks under the same conditions. A team policy sets the CRAP limit. [Built-in tools](docs/built-in-tools.md) gives the capability name, timing and needs of each check.

### Your own tools

A team policy can [add other tools](docs/add-a-tool.md) of five kinds:

| Kind | What gateseal runs | Example |
| --- | --- | --- |
| Hook repository | A pre-commit hook, pinned by commit ID | [Black](docs/add-a-tool.md#offer-black-as-a-formatter) |
| Release binary | A downloaded program, checked by SHA-256 | [Gitleaks](docs/add-a-tool.md#add-gitleaks) |
| uv project | A Python tool that imports the project | [mypy](docs/add-a-tool.md#add-mypy-a-tool-that-imports-the-project) |
| npm package | A tool from npm, on a pinned Node | [Prettier for Markdown](docs/add-a-tool.md#format-markdown-with-prettier-from-npm) |
| Node project | A tool from npm that reads the project's packages | [`tsc` with strict flags](docs/add-a-tool.md#check-a-second-tsconfig-with-strict-tsc-flags) |

A repository can also run its own script as a check. See [Adjust a setup](docs/adjust-a-setup.md).

### CI and platforms

| | Supported | Planned |
| --- | --- | --- |
| CI | GitHub Actions, Azure Pipelines | |
| Platforms | macOS on arm64, Linux on x86-64 | Windows |
| Package managers | uv; npm and pnpm | |
| Tool updates | `gateseal upgrade` to a reviewed version | Pull requests for new tool releases |

## What you can do with it

| Goal | How | Guide |
| --- | --- | --- |
| Install gateseal | `uv tool install gateseal` | [Install gateseal](docs/install.md) |
| See a check work first | A scratch repository | [Try gateseal](docs/demo.md) |
| Change the checks | `capabilities` in `gateseal.toml`, then `gateseal configure` | [Configure a repository](docs/configure-a-repository.md) |
| Commit in a repository that uses gateseal | `pre-commit install`, no gateseal needed | [Work in a configured repository](docs/work-in-a-configured-repository.md) |
| Run slow checks at a push | A check at push, such as `python-tests`, then `pre-commit install` in each clone | [Checks at a push](docs/work-in-a-configured-repository.md#checks-at-a-push) |
| Block a merge that fails a check | `--ci github` or `--ci azure`, then make the check required | [GitHub Actions](docs/github-ci.md), [Azure Pipelines](docs/azure-pipelines.md) |
| Move to a new tool or policy release | `gateseal upgrade` with a policy tag or a tool version | [Upgrade and roll back](docs/upgrade.md) |
| Use one standard in many repositories | `--policy <URL> --policy-tag <tag>` | [Adopt a team policy](docs/adopt-a-team-policy.md) |
| Publish a team standard | `gateseal policy init <dir>`, commit, then tag a release | [Author a team policy](docs/author-a-policy.md) |
| Let a coding agent read the results | `--json` on any command | [Commands and results](docs/commands-and-results.md) |
| Fix a check that stops or does not run | Look up the symptom | [Troubleshoot](docs/troubleshooting.md) |
| Learn the design | The Manifest, the Lock and the policy | [How gateseal works](docs/architecture.md) |

[Contribute](CONTRIBUTING.md) · [Report a vulnerability](SECURITY.md) · [MIT license](LICENSE)
