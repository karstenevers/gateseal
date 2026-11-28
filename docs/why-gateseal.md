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
| Runs tests with coverage, CRAP scores, and mutation tests | With hooks you write | No | No | No | Yes, for Python and TypeScript, with [built-in tools](built-in-tools.md) |
| Pins release binaries, such as Betterleaks, and Node by checksum | No | No | No | No | Yes |
| Installs tools from npm, such as ESLint, from a lockfile with no install scripts | No | No | No | No | [Yes](#pre-commits-node-hooks) |
| Updates hook versions to | The latest tag, with `pre-commit autoupdate` | The latest tag, in weekly pull requests, only GitHub | The latest release, in pull requests, only GitHub | The latest allowed release, in pull requests | The team's reviewed release, with `gateseal upgrade` |
| Updates pre-commit and binaries too | No | No | No | Manual PR setup | Yes, with `gateseal upgrade` |
| Finds hand edits to the setup | No | No | No | No | Yes, on each commit and CI run |
| Shares one standard across repositories | No | No | No | Update rules only | Yes, as a policy release |

## Plain pre-commit

pre-commit installs and runs the hooks, and gateseal uses it for the same job.

A pre-commit config usually names each hook by a tag, and the hook's owner can move a tag. gateseal always pins hooks by commit ID, so a moved tag cannot change what runs.

pre-commit does not install itself, Python, or release binaries such as Terraform or TFLint. Each dev machine, CI setup step, and build agent installs them by hand. Over time, these versions drift apart, and a check that passes on one machine fails on another.

gateseal records these versions in the Lock, `gateseal.lock`. It generates `.pre-commit-config.yaml` and the CI file from the same Lock, so the two cannot drift apart. The Lock check fails on a hand edit to either file. On each dev machine and in CI, mise installs each release binary at the Lock's version and checks its archive.

## pre-commit's Node hooks

Without gateseal, a repository can run ESLint and Prettier as pre-commit hooks with `language: node`. The last two rows show where pre-commit is equal or better.

| | gateseal | pre-commit `language: node` |
| --- | --- | --- |
| What you write | One capability name | A hook block for each tool, with its version and packages |
| Node version | The policy's pin, checked by SHA-256 | The hook's `language_version`, without a checksum |
| The tool's packages | Locked by npm's lockfile, each checked by its integrity hash | Resolved again at each install |
| Install scripts of packages | Never run | Run |
| Packages that `tsc` reads | Installed from the project's lockfile at each push | The packages in `node_modules`, which nothing installs |
| Later commits | A little slower | A little faster |
| ESLint presets | The rules of each preset only | Each preset with all its settings |

## pre-commit.ci

pre-commit.ci is a hosted service that runs pre-commit on GitHub pull requests and opens weekly pull requests that update the hook versions. It runs only on GitHub and only in CI. It does not install release binaries, and it does not check the setup for hand edits.

## Dependabot and Renovate

Dependabot and Renovate open pull requests when a dependency has a new release. They do not run checks. Dependabot runs only on GitHub. Renovate also runs on Azure DevOps.

Dependabot's `pre-commit` updates change only the `rev:` of each hook, not the pre-commit version or the version of a binary. Renovate can update more, with custom rules that each team writes and maintains.

`gateseal upgrade` moves the hook versions and each binary with its checksum in one commit. A team policy reviews these versions once for every repository. See [Upgrade and roll back](upgrade.md).

> [!NOTE]
> In a repository that gateseal configures, do not let Dependabot or Renovate change `.pre-commit-config.yaml` or the generated CI file. The Lock check fails on the edit. Use `gateseal upgrade`. Both tools can still update the application's own dependencies.

## Made for coding agents

A coding agent commits through the same pinned checks as a person. gateseal commands do not stop for prompts. With `--json`, each result says what failed, why, and the next step. An agent can then fix the problem without a person.
