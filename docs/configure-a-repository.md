# Configure a repository

Add the bundled checks to a Git repository. If it already has `gateseal.lock`, follow [Work in a configured repository](work-in-a-configured-repository.md) instead.

[Install gateseal](install.md), pre-commit, and mise first. From the repository root, select the hygiene and secrets checks and GitHub Actions:

```sh
gateseal configure --capability hygiene --capability secrets --ci github
pre-commit install
```

The command writes `gateseal.toml`, the Manifest, which selects the policy and checks. It also writes `gateseal.lock`, the Lock, which records the selection and the generated files. See [an example Manifest](policy-contract.md#example-with-the-bundled-policy).

## Select checks

Add one `--capability` option for each group of checks. [Built-in tools](built-in-tools.md) lists every capability of the bundled policy, with what it checks and what it needs. For example, `python` adds Ruff, and `typescript` adds ESLint and Prettier.

## Select CI

`--ci github` writes a GitHub Actions workflow. For Azure Pipelines, use `--ci azure` and follow [Azure Pipelines](azure-pipelines.md). Leave out `--ci` if you use neither.

## Commit the files

Review the Manifest, the Lock, `.pre-commit-config.yaml`, the files under `.gateseal/`, and the CI file. Then commit them together:

```sh
git add gateseal.toml gateseal.lock .pre-commit-config.yaml \
  .gateseal .github/workflows/gateseal.yml
git commit -m 'Adopt gateseal checks'
```

Without `--ci`, leave out `.github/workflows/gateseal.yml`. With `--ci azure`, add `azure-pipelines-gateseal.yml` in its place.

The first commit downloads the hook environments. If a hook changes a file, review the change. Stage it. Commit again. If a check stops, see [Troubleshoot gateseal](troubleshooting.md).

## Change the checks

To change the selection later, edit `capabilities` in `gateseal.toml`. For example, set it to `['hygiene', 'python']` to drop `secrets` and add Ruff. Then run:

```sh
gateseal configure
```

Review the changed files. Commit them together. See [Adjust a setup](adjust-a-setup.md) for repository hooks and policy choices.
