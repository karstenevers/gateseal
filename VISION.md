# gateseal vision

> One reviewed policy for repository checks, compiled into every repository, upgraded through pull requests.

gateseal is a policy compiler for repository checks. A team reviews its check
tools, their versions and their rules once, as a versioned Policy. Each
repository compiles that Policy into the native configuration of pre-commit,
mise and its CI platform, and moves to a new release through a reviewed pull
request.

This page describes where the project is going. The current release implements
file hygiene, Ruff checks for Python, Betterleaks secret detection and a GitHub
Actions gate. The [README](README.md) has the working quickstart.

## The problem

A team with twenty repositories maintains twenty slightly different copies of
the same checks. Ruff rules drift between repositories. The secret scanner is
pinned to three different versions, each with its own allowlist. A new TFLint
release breaks one pipeline, and nobody notices until a Friday deploy. An
upgrade means editing every repository by hand, and a new contributor starts by
installing seven tools at exact versions.

Existing tools each solve a part. pre-commit runs hooks, mise installs tools,
and Renovate bumps versions. Lefthook and hk share hook configuration, but each
repository still pins its own tools. gateseal adds the missing part, which is
one reviewed release that fixes tool versions, rules, compatibility constraints
and CI inputs together. It builds on those tools for everything else.

## How it works

- **Manifest.** A repository commits a small file that says what it checks:
  the Policy release, capabilities such as Python or secret scanning, and the
  choices that the Policy exposes.
- **Policy.** A team publishes a versioned data package with the reviewed tool
  versions, rule files, compatibility constraints and CI inputs. A Policy
  holds declarative data, and the compiler never loads scripts or plugins from
  it. A reference Policy ships with the package, and a team publishes its own
  in Git.
- **Compiler.** gateseal compiles the Manifest and the Policy into files that
  existing tools read: `.pre-commit-config.yaml`, `ruff.toml`, the secret
  scanner's rules, a managed mise configuration, and a CI workflow with one
  stable required check.
- **Lock.** A committed Lock records every direct input and the hash of every
  generated file. gateseal reports an edited or outdated file and never
  overwrites it silently.
- **Upgrade.** One command plans the move to another Policy release before it
  writes anything, and reports what changed. A workflow or dependency bot
  delivers the result as a pull request that a person reviews. A rollback is
  the same command with an older release.

gateseal delegates the rest. pre-commit runs the hooks, mise installs the
binaries, uv prepares Python, and the CI platform runs the gate.
[Each part has one owner](docs/architecture.md#each-part-has-one-owner) lists
who owns what.

## Who gateseal is for

gateseal serves developers, repository owners, platform teams and coding
agents. The [README](README.md#who-gateseal-is-for) says what each of them
gets.

## Principles

1. **Generated configuration.** Shared configuration comes from one definition
   and is never pasted between repositories.
2. **Policy as a dependency.** A Policy release has a version, a changelog,
   reviewed compatibility rules and a Lock entry, like any other dependency.
3. **No silent behavior.** gateseal upgrades nothing implicitly, detects no
   capability by itself, falls back to no other tool version, and skips no
   check when a tool is missing. Each such state fails with a named recovery
   step.
4. **The same checks locally and in CI.** The commit hook, the manual full run
   and the CI gate run the same compiled Policy, each on its own scope.
5. **Delegation.** gateseal installs no tools, builds no containers, runs no
   hooks, opens no pull requests and administers no CI platform. Mature
   open-source tools do, and gateseal drives them.
6. **Native files.** Generated files are ordinary tool configuration that the
   native tools can read. A repository that leaves gateseal keeps them and
   replaces gateseal's hook entries with native ones.
7. **Stated limits.** The Lock pins the direct inputs that gateseal controls.
   It does not claim hermetic runners or frozen transitive dependencies.

## Out of scope

- Running hooks, installing tools and managing versions. gateseal renders
  configuration for pre-commit and mise.
- Containers for commit hooks. A commit hook never runs in one.
- Dependency updates. gateseal generates the upgrade, and Renovate or a
  workflow delivers it.
- A hosted service. gateseal has no account and no dashboard, and it sends no
  data anywhere.
- A linter collection with hundreds of plugins. Capabilities are explicit,
  reviewed and few.

## Later capabilities

Later releases add capabilities for SQL, Terraform, infrastructure security
and dependency audits, an Azure Pipelines gate beside the GitHub one, an
optional pinned CI image, artifact mirrors for networks that cannot reach the
public sources, and further tool kinds such as Node packages.
