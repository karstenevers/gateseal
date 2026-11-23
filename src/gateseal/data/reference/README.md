# gateseal policy

This directory is a gateseal policy. Repositories adopt one release of it, a
tag of this repository, and their commits run the checks it describes.
gateseal reads only `policy.toml`, the files it names under `rules/`, the
definitions of the tools it adds under `tools/`, and `policy.lock`. It ignores
this README and every other file.

## Edit

- `policy.toml` holds a table for each tool, under its capability, with the
  hooks it runs, its reviewed versions and its pin. Each capability's
  `choices` gives the choices a repository may make and their defaults. Set
  `release` to a label of your own for each release.
- `tools/<name>.toml` defines a tool that gateseal does not ship, of a kind it
  implements. A tool named like one gateseal ships always runs gateseal's
  definition.
- `node` at the top of `policy.toml` pins the Node that every tool from npm
  runs on. The `typescript` capability runs ESLint and then Prettier on it.
  The `typescript-types` capability runs tsc on it in the repository's own npm
  or pnpm project, whose lockfile pins the packages that tsc reads. Each
  tool's `packages` table pins the npm packages that it installs beside
  itself, such as the parser that the ESLint rules name.
- `rules/ruff.toml`, `rules/pyright.toml` and `rules/betterleaks.toml` are
  the Ruff configuration, the Pyright configuration and the Betterleaks rules
  that configure copies into each repository. Pyright reads the paths in its
  file relative to `.gateseal/`, where configure writes it. Its `venvPath`
  and `venv` name `environments/pyright`, the environment in which the
  Pyright hook runs.
  The Betterleaks rules start from the rules built into Betterleaks, and
  `disabledRules` turns some of them off.
- `rules/crap.toml` holds only `limit`, the highest CRAP score that a
  function may have. configure copies it to `.gateseal/crap.toml`, which the
  CRAP hook reads.
- `rules/eslint.toml` holds the ESLint rules: the files to check, the parser
  and plugins by package, and each rule's severity. It holds only `files`,
  `parser`, `plugins` and `rules`, and names only packages that the `packages`
  of `eslint` pin. Configure writes it as `.gateseal/eslint.json`.
- `rules/prettier.toml` holds the Prettier options that configure copies
  into each repository.
- `rules/uv-audit.toml` lists the advisories that the audit ignores, under
  `[audit]` as `ignore`. Give each one's reason in a comment. configure copies
  it to `.gateseal/uv-audit.toml`, and uv reads only this file, so a
  repository cannot ignore an advisory.
- `gateseal policy add <tool> <version>` reviews a new version, and
  `gateseal policy pin <tool> <version>` makes a reviewed version the pin.
  Both edit only their own lines of `policy.toml` and keep its comments.

## Generate

- `gateseal policy lock` writes `policy.lock`. It reads each reviewed
  version's commit from its release tag and each archive's SHA-256 from the
  release's checksum file, and downloads each new archive to check it. It
  runs npm on the policy's Node to record each npm tool's lockfile: every
  package that npm installs, with its integrity hash.
  `gateseal policy add` writes it the same way. Run lock after you add or
  remove a reviewed version by hand, and never edit `policy.lock` by hand.
- `gateseal policy check` proves offline that `policy.lock` agrees with
  `policy.toml`. Run it before you commit. Configure and check refuse a
  policy that fails it.

`gateseal policy lock` never changes a fact that `policy.lock` already
records. When upstream changes one, such as a tag that moved, it stops and
names both values. To accept the new value, delete `policy.lock`, run
`gateseal policy lock`, and review every change in the diff of
`policy.lock`.

## Release

Commit the policy, then tag the commit. Repositories adopt it with
`gateseal configure --policy <repository URL> --policy-path <this directory>
--policy-tag <tag>`.
