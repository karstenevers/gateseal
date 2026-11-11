# gateseal policy

This directory is a gateseal policy. Repositories adopt one release of it, a
tag of this repository, and their commits run the checks it describes.
gateseal reads only `policy.toml`, the files it names under `rules/`, the
definitions of the tools it adds under `tools/`, and `policy.lock`. It ignores
this README and every other file.

## Edit

- `policy.toml` names the tools of each capability and the hooks of each
  tool, declares the choices a repository may make, and lists each
  component's reviewed versions and its pin. Set `release` to a label of your
  own for each release.
- `tools/<name>.toml` defines a tool that gateseal does not ship, of a kind it
  implements. A tool named like one gateseal ships always runs gateseal's
  definition.
- `rules/ruff.toml` and `rules/betterleaks.toml` are the Ruff configuration
  and the Betterleaks rules that configure copies into each repository.
- `gateseal policy add <tool> <version>` reviews a new version, and
  `gateseal policy pin <tool> <version>` makes a reviewed version the pin.
  Both edit only their own lines of `policy.toml` and keep its comments.

## Generate

- `gateseal policy lock` writes `policy.lock`. It reads each reviewed
  version's commit from its release tag and each archive's SHA-256 from the
  release's checksum file, and downloads each new archive to check it.
  `gateseal policy add` writes it the same way. Run lock after you add or
  remove a reviewed version by hand, and never edit `policy.lock` by hand.
- `gateseal policy check` proves offline that `policy.lock` agrees with
  `policy.toml`. Run it before you commit. Configure and activation refuse a
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
