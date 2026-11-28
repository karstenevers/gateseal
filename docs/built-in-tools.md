# Built-in tools

The bundled policy has a definition for each tool below. A capability turns its tool on. Name it when you configure the repository:

```sh
gateseal configure --capability hygiene --capability python
```

In a configured repository, add the name to `capabilities` in `gateseal.toml` and run `gateseal configure`. A team policy can [add other tools](add-a-tool.md).

| Capability | Tool | What it checks | Runs at | Needs |
| --- | --- | --- | --- | --- |
| `hygiene` | pre-commit-hooks | Whitespace, file endings and YAML | Commit | |
| `secrets` | Betterleaks | Secrets, such as cloud keys, private keys and service tokens | Commit | mise |
| `python` | Ruff | Python lint and format | Commit | |
| `python-types` | Pyright | Python types, in strict mode | Commit | uv project |
| `python-lock` | uv | `uv.lock` matches `pyproject.toml` | Commit | uv project |
| `python-dependencies` | deptry | Unused and missing dependencies | Commit | uv project |
| `python-audit` | uv | Known vulnerabilities of the locked packages | CI only | uv project |
| `python-tests` | coverage.py with pytest | The tests pass | Push | uv project |
| `python-crap` | CRAP score | No function in a changed file scores over 10 | Push | `python-tests` |
| `python-mutation` | pytestmut | The tests catch changes to the changed lines | Push | uv project |
| `typescript` | ESLint, then Prettier | Lint and format of `.ts` and `.tsx` files | Commit | mise |
| `typescript-types` | `tsc` | TypeScript types | Push | Node project, `tsconfig.json` |
| `typescript-typed-lint` | ESLint | Lint rules that need type information | Push | Node project, `tsconfig.json` |
| `typescript-unused` | knip | Unused files, exports and dependencies | Push | Node project |
| `typescript-vitest` or `typescript-jest` | Vitest or Jest | The tests pass | Push | Node project. Select only one of the two |
| `typescript-crap` | CRAP score | No function in a changed file scores over 10 | Push | One of the two test capabilities |
| `typescript-mutation` | StrykerJS | The tests catch changes to the changed lines | Push | Node project |
| `typescript-audit` | OSV-Scanner | Known vulnerabilities of the packages in `package-lock.json` | CI only | `package-lock.json`, mise |

Each check also runs in the [CI gate](ci-gate.md). A check at a commit also runs in `gateseal run`.

## What the checks need

- **uv project.** `pyproject.toml` and `uv.lock` at the repository root, and uv on each machine. `python-lock` brings its own uv. For the tests, pytest is in a dependency group that uv installs by default.
- **Node project.** `package.json` and exactly one lockfile at the repository root: `package-lock.json` for npm, or `pnpm-lock.yaml` for pnpm. Each machine needs mise, but no Node or npm. For the tests, the project lists its own test runner.
- **Checks at a push.** Each clone needs the [push hook](work-in-a-configured-repository.md#checks-at-a-push).
- **CRAP score.** The coverage report must measure each changed file. So list each folder of code in the coverage settings of coverage.py, Vitest or Jest.
- **Audits.** The CI machine needs network access to `api.osv.dev`.

## What you can change

- **Paths.** To keep paths from a capability with an `exclude` choice, such as `typescript`, add this table to `gateseal.toml`:

  ```toml
  [choices.typescript]
  exclude = ["vendor"]
  ```

- **A function over the CRAP limit.** List it with its current score in `.crap-baseline.toml` at the repository root, and commit that file on its own. A later change may lower or remove an entry, but not add or raise one.

  ```toml
  [baseline."src/shop/pricing.py"]
  shipping = 20
  ```

- **Rules and versions.** The repository's own ESLint, Prettier and knip settings do not apply. A team changes rules, pins and ignored advisories in [its own policy](author-a-policy.md).
- **Another reviewed version.** Add a [version exception](policy-contract.md#version-exceptions).

## Limits

- **Node projects.** Each check reads one project at the repository root. Yarn, workspaces and project references are not supported.
- **Node installs.** A check of a Node project at a push first installs the packages that the project's lockfile pins. No install script runs. A lockfile that does not match `package.json` stops the push. After a change of the package manager, delete `node_modules` one time.
- **Private npm packages.** The generated CI file gives npm and pnpm no registry token.
- **Audits.** A new advisory can fail a CI run without any commit. `typescript-audit` reads only `package-lock.json`, and does not check that it matches `package.json`. So select a check at a push beside it.
- **Test checks.** They start only when a checked file is a Python file, or a TypeScript or JavaScript file. Keep your own test job for other changes, such as a lockfile update or a change that only deletes files.
- **Mutation tests.** A run without a range, such as a weekly run or a new branch, tests no line. pytestmut stops after 40 changes or 180 seconds by default. StrykerJS changes only code in `src` and `lib`, unless `mutate` in the repository's StrykerJS configuration names other files.
