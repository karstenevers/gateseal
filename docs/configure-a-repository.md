# Configure a repository

Add the bundled checks to a Git repository. If it already has `gateseal.lock`, follow [Work in a configured repository](work-in-a-configured-repository.md) instead.

[Install gateseal](install.md), pre-commit, and mise first. From the repository root, select the hygiene and secrets checks and GitHub Actions:

```sh
gateseal configure --capability hygiene --capability secrets --ci github
pre-commit install
```

The command writes `gateseal.toml`, the Manifest, which selects the policy and checks. It also writes `gateseal.lock`, the Lock, which records the selection and the generated files. See [an example Manifest](policy-contract.md#example-with-the-bundled-policy).

## Select checks

Add one `--capability` option for each group of checks:

| Capability | What it checks | Needs |
| --- | --- | --- |
| `hygiene` | Whitespace, file endings, and YAML. | |
| `secrets` | Secrets, with Betterleaks. Its rules find cloud keys, private keys, passwords, and many service tokens. | mise |
| `python` | Python lint and format, with Ruff. A commit can change the format of a Python file. | |
| `python-types` | Python types, with Pyright in strict mode, at every commit. | A uv project with `uv.lock` |
| `typescript` | TypeScript lint and format, with ESLint and Prettier. See [Check TypeScript](typescript.md). | mise |
| `typescript-types` | TypeScript types, with `tsc`, at each push. See [Type checks at push](typescript.md#type-checks-at-push). | An npm or pnpm project, and mise |
| `typescript-unused` | Unused files, exports, and dependencies, with knip, at each push. See [Dependency checks](typescript.md#dependency-checks). | An npm or pnpm project, and mise |
| `typescript-audit` | Known vulnerabilities of the packages in `package-lock.json`, with OSV-Scanner, in the CI gate. See [The audit](typescript.md#the-audit). | An npm project with `package-lock.json`, and mise |

To run the tests at each push, follow [Check Python tests](python-tests.md) or, for Vitest or Jest, [Test checks at push](typescript.md#test-checks-at-push). To check `uv.lock`, unused dependencies, and known vulnerabilities, follow [Check Python dependencies](python-dependencies.md).

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
