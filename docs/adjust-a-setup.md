# Adjust a setup

Change checks in `gateseal.toml`, the Manifest that selects the policy and checks. Run `gateseal configure` after each edit. Review the Manifest, `gateseal.lock`, and generated files. Commit them together. The Lock records the selected policy and generated files.

## Add a repository hook

A repository hook runs a script from the repository after policy hooks. For example, add this table to `gateseal.toml`:

```toml
[hooks.check-migrations]
name = "Check migration numbers"
run = "scripts/check-migrations.sh"
args = ["--strict"]
files = "^migrations/"
```

Commit the executable script with a `#!` line. The script receives matching staged paths after its configured arguments. It also runs in `gateseal run` and CI. gateseal does not install its dependencies. See [repository hook fields](policy-contract.md#repository-hooks) for restrictions.

## Skip an optional hook

A policy may allow a repository to skip selected hooks. For example, if the policy marks `check-yaml` optional, add:

```toml
[choices.hygiene]
skip = ["check-yaml"]
```

Only hooks the policy marks optional can be skipped. The bundled policy marks none optional. See [optional hooks](policy-contract.md#skip-optional-hooks).

## Make other changes

- Select checks with [`capabilities`](policy-contract.md#capabilities).
- Set policy-exposed values with [`choices`](policy-contract.md#choices).
- Select a reviewed tool version with a [version exception](policy-contract.md#version-exceptions).
- Change rules or add a tool for the team in a [policy release](author-a-policy.md).
- Add a [personal hook](work-in-a-configured-repository.md#add-your-own-hooks) for checks on your machine alone.

Do not edit generated files. The commit hook and CI check them against the Lock.
