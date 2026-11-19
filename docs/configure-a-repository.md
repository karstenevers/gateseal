# Configure a repository

Add the bundled checks to a Git repository. If it already has `gateseal.lock`, follow [Work in a configured repository](work-in-a-configured-repository.md).

[Install gateseal](install.md), pre-commit, and mise first. From the repository root, select the hygiene and secrets checks and GitHub Actions:

```sh
gateseal configure --capability hygiene --capability secrets --ci github
pre-commit install
```

`hygiene` checks whitespace, file endings, and YAML. `secrets` runs Betterleaks, which mise installs. Its built-in rules find cloud keys, private keys, passwords and many service tokens, and the bundled policy adds a rule for an AWS access key ID alone. In a Python repository, add `--capability python` for Ruff. When you commit Python files, Ruff can change their formatting. In a uv project, add `--capability python-types` for Pyright in strict mode. Pyright runs at every commit in the project's own environment, so the repository needs a `uv.lock`. In a TypeScript repository, add `--capability typescript` for ESLint and Prettier, as [Check TypeScript](typescript.md) shows. For Azure Pipelines, use `--ci azure` and follow [Azure Pipelines](azure-pipelines.md). Omit `--ci` if you use neither. The first command writes `gateseal.toml`, the Manifest that selects the policy and checks. It also writes `gateseal.lock`, the Lock that records the selection and generated files.

See [an example Manifest](policy-contract.md#example-with-the-bundled-policy) for how it looks. Review the Manifest, Lock, `.pre-commit-config.yaml`, and the CI file if generated. If you omitted `--ci`, omit `.github/workflows/gateseal.yml` from the next command. With `--ci azure`, add `azure-pipelines-gateseal.yml` in its place. Then commit them:

```sh
git add gateseal.toml gateseal.lock .pre-commit-config.yaml \
  .gateseal .github/workflows/gateseal.yml
git commit -m 'Adopt gateseal checks'
```

The first commit downloads the hook environments. If a hook changes a file, review the change. Stage it. Retry the commit. See [Troubleshoot gateseal](troubleshooting.md) if a check stops.

## Change the checks

Each `--capability` option adds one capability. Without `secrets` and `typescript`, the repository needs no mise. To change the selection later, edit `capabilities` in `gateseal.toml`. For example, set it to `['hygiene', 'python']` to drop `secrets` and add Ruff. Then run:

```sh
gateseal configure
```

Review the Manifest, Lock, and generated files. Commit them together. Python, Pyright, secrets and TypeScript also generate files under `.gateseal/`. See [Adjust a setup](adjust-a-setup.md) for repository hooks and policy choices.
