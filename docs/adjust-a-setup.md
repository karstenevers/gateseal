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

A check that is too slow for each commit can run at `git push` instead. Add this line to its table:

```toml
stages = ["push", "ci"]
```

The script then receives the paths that the push changes. It still runs in CI. Each clone needs the push hook. See [Checks at a push](work-in-a-configured-repository.md#checks-at-a-push).

A script that checks or rebuilds the whole project, such as a code generator, should run once and receive no paths. Add this line:

```toml
scope = "project"
```

It then runs once when a checked file matches `files`: a staged file at a commit, a changed file in a CI gate run on a range, or any tracked file in `gateseal run` and in a CI run that checks all files. See [project hooks](policy-contract.md#repository-hooks).

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
