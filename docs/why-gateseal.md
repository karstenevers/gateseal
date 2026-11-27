# Why gateseal

gateseal makes one reviewed set of checks easy to install, pin, and update in many repositories. Every version that a check needs is in one file, and `gateseal upgrade` moves them. Each dev machine, CI job, and coding agent then runs the same checks.

For example, move OpenTofu to a new release in 20 repositories, while 2 of them stay on the old version for now:

- **Without gateseal**, you edit 18 `.pre-commit-config.yaml` files and 18 CI setup steps. The usual OpenTofu hooks run whichever `tofu` is on the PATH. A developer who works in both kinds of repository installs both versions with a version manager and switches before each commit. If they forget, the hook runs the wrong version. Nothing tells you which repository you missed.
- **With gateseal**, the platform team reviews the new version once in a policy release. 18 repositories run `gateseal upgrade --policy-tag team-2026.12` and commit the result. The other 2 keep their old release. If the policy still reviews the old OpenTofu, they can also take the new release. They then keep the old OpenTofu as a [Version Exception](policy-contract.md#version-exceptions) with a review date. Each hook runs the OpenTofu from its own repository's Lock, so developers do nothing.

## Compare the options

| | pre-commit alone | pre-commit.ci | Dependabot | Renovate | gateseal |
| --- | --- | --- | --- | --- | --- |
| Runs the checks at commit time | Yes | No | No | No | Yes, through pre-commit |
| Runs the checks in CI | With a CI job you write | Yes, only GitHub | No | No | Yes, in a CI file it writes |
| Checks only the changed files in CI | With refs you set yourself | Not documented | No | No | Yes, for pull requests, and for pushes on GitHub |
| Runs Python or TypeScript tests with coverage, CRAP scores, and mutation tests | With hooks you write | No | No | No | Yes, for [Python](python-tests.md) and [TypeScript](typescript.md#test-checks-at-push), at a push and in CI. Coverage and CRAP share one test run |
| Pins release binaries, such as Betterleaks, and Node by checksum | No | No | No | No | Yes |
| Installs tools from npm, such as ESLint, from a lockfile with no install scripts | No | No | No | No | [Yes](typescript.md#gateseal-and-pre-commits-node-hooks) |
| Updates hook versions to | The latest tag, with `pre-commit autoupdate` | The latest tag, in weekly pull requests, only GitHub | The latest release, in pull requests, only GitHub | The latest allowed release, in pull requests | The team's reviewed release, with `gateseal upgrade` |
| Updates pre-commit and binaries too | No | No | No | Manual PR setup | Yes, with `gateseal upgrade` |
| Finds hand edits to the setup | No | No | No | No | Yes, on each commit and CI run |
| Shares one standard across repositories | No | No | No | Update rules only | Yes, as a policy release |

## Plain pre-commit

pre-commit installs and runs the hooks. It keeps hook environments in `~/.cache/pre-commit`, so later runs are fast. gateseal uses pre-commit for the same job.

A pre-commit config usually names each hook by a tag, and the hook's owner can move a tag. `pre-commit autoupdate --freeze` writes commit IDs instead. gateseal always pins hooks by commit ID, so a moved tag cannot change what runs.

pre-commit does not install itself, Python, or release binaries such as Terraform or TFLint. Each dev machine, CI setup step, and build agent installs them by hand. Over time, these versions drift apart, and a check that passes on one machine fails on another. Some tools, such as Betterleaks, avoid this with a hook that pre-commit builds from source with Go. For ESLint, Prettier and other tools from npm, see [gateseal and pre-commit's Node hooks](typescript.md#gateseal-and-pre-commits-node-hooks).

gateseal records these versions in the Lock, `gateseal.lock`. It generates `.pre-commit-config.yaml` and the CI file from the same Lock, so the two cannot drift apart. The Lock check fails on a hand edit to either file. In CI and on each dev machine, mise installs each release binary at the Lock's version and checks its archive. The CI file downloads the pinned uv and mise and checks each against its SHA-256. It installs gateseal from the commit that the Lock names, and that release installs its pinned pre-commit. On a dev machine, `gateseal setup` installs the pinned pre-commit, or the developer keeps their own at the minimum version.

## pre-commit.ci

pre-commit.ci is a hosted service that runs pre-commit on GitHub pull requests. It is free for public repositories. It also opens weekly pull requests that update the hook versions.

It runs only on GitHub and only in CI. It does not install release binaries outside pre-commit, and it does not check the setup for hand edits. gateseal runs the same checks on commit and in your own GitHub Actions or Azure Pipelines job.

## Dependabot and Renovate

Dependabot and Renovate open pull requests when a dependency has a new release. They do not run checks. Dependabot runs only on GitHub. Renovate also runs on Azure DevOps.

Dependabot's `pre-commit` updates change only the `rev:` of each hook. They do not change `additional_dependencies` or the pre-commit version. Its GitHub Actions updates change `uses:` lines, not inputs such as `terraform_version`. Renovate's `pre-commit` manager, which you turn on, also updates the `additional_dependencies` of Go, Node and Python hooks that name their `language`. For the pre-commit version and inputs such as `terraform_version`, Renovate needs custom regular expression rules, which each team writes and maintains.

A policy upgrade with `gateseal upgrade` moves the hook versions and each binary with its checksum in one commit. An upgrade of gateseal itself moves the pre-commit, uv, and mise that it pins. A team policy reviews these versions once for every repository. See [Upgrade and roll back](upgrade.md).

> [!NOTE]
> In a repository that gateseal configures, do not let Dependabot or Renovate change `.pre-commit-config.yaml` or the generated CI file. The Lock check fails on the edit. Use `gateseal upgrade`. Both tools can still update the application's own dependencies.

## Made for coding agents

A coding agent commits through the same pinned checks as a person. gateseal commands do not stop for prompts. With `--json`, each result says what failed, why, and the next step. An agent can then fix the problem without a person.
