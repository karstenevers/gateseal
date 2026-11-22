# Check TypeScript

The bundled `typescript` capability checks staged `.ts` and `.tsx` files with ESLint and then formats them with Prettier. Both tools run on a Node that the policy pins. So a commit needs no Node or npm on the machine. The checks run at each commit, in `gateseal run`, and in the CI gate.

## Select the capability

[Install gateseal](install.md), pre-commit and mise first. From the repository root, run:

```sh
gateseal configure --capability hygiene --capability typescript --ci github
pre-commit install
```

In a configured repository, add `"typescript"` to `capabilities` in `gateseal.toml` and run `gateseal configure` instead.

Besides the Manifest, the Lock and `.pre-commit-config.yaml`, configure writes four files under `.gateseal/`:

- `eslint.json`, the ESLint rules.
- `eslint-loader.mjs`, the file that gives the rules to ESLint.
- `eslint-suppressions.json`, an empty list that the hook gives to ESLint in place of the repository's own `eslint-suppressions.json`.
- `prettier.toml`, the Prettier options.

Review the files and commit them together, as [Configure a repository](configure-a-repository.md) shows. To see the versions that the hooks run, run `gateseal status`. Its output includes lines like this:

```text
Tool eslint 10.15.0 for typescript: built-in, npm-package on Node 24.22.3.
```

## What each developer needs

Each developer needs Git, pre-commit 4.7.0 or later, and mise 2026.10.19 or later. Node, npm and gateseal are not needed. The hooks start the Node that the Lock records, whatever Node is on `PATH`. This needs mise's auto-install, which is on by default. With it off, mise starts the `node` on `PATH` until the pinned Node is installed. Each hook then stops and says that mise has no install of the pinned Node, and `gateseal doctor` warns.

The first commit that stages a TypeScript file downloads these files:

- The pinned Node from nodejs.org. mise installs it and checks its SHA-256 against the Lock.
- ESLint, typescript-eslint's parser and plugin, TypeScript and Prettier from the npm registry. npm installs them once into `gateseal/npm` in your cache folder, outside the repository.

Later commits call no npm and need no network, until a pin changes. The CI gate needs the same network access. Two repositories that pin different Node versions each run their own Node and their own install of the packages on one machine.

## What a commit does

1. ESLint checks each staged TypeScript file and applies the fixes that its rules have, such as `let` to `const`.
2. Prettier formats the same files.

If a hook changes a file, the commit stops. Review the change, stage it and commit again. A finding that ESLint cannot fix also stops the commit. ESLint's report names the file, the line and the rule, such as `@typescript-eslint/no-explicit-any`.

ESLint runs first, so Prettier formats what the fixes leave. The bundled ESLint rules hold no rule about formatting, and they leave out `no-unexpected-multiline`, which conflicts with Prettier. So the two tools do not undo each other's changes.

The bundled rules are ESLint's recommended rules and typescript-eslint's recommended rules. They leave out the core rules that typescript-eslint turns off for TypeScript. The Prettier options are Prettier's defaults, such as a line width of 80 and double quotes.

## What the repository cannot change

The hooks ignore the repository's own settings and packages:

- **ESLint configuration.** ESLint gets gateseal's loader with `--config`, so an `eslint.config.js` of the repository does not run.
- **Prettier settings.** Prettier gets the policy's options with `--config`, and ignores `.prettierrc`, `.prettierignore` and `.editorconfig`.
- **Node project.** npm runs outside the repository's Node project. Its `package.json`, `.npmrc` and `node_modules` do not choose or replace a package. A pnpm project without `node_modules` works too.

The rules change only through the policy. A comment in a file, such as `// eslint-disable-next-line`, still turns a rule off for that line or file, and the change's diff shows it. An `eslint-suppressions.json` file of the repository accepts no finding, because the hook does not read it. A repository can skip paths, and it can select another version that the policy reviewed with a [version exception](policy-contract.md#version-exceptions).

## Skip paths

To keep files from both tools, add this table to `gateseal.toml`:

```toml
[choices.typescript]
exclude = ["vendor"]
```

Run `gateseal configure` and commit the changed files. Each path names a file or a folder from the repository root. The hooks also never get a file that gateseal generates.

## Change the rules or the versions

A team changes the checks in its own policy release. Start one with [Author a team policy](author-a-policy.md). The bundled policy copy holds these files:

- `rules/eslint.toml` holds the ESLint rules. See [the ESLint format](tool-definitions.md#eslint-configuration-format) for what the file can hold.
- `rules/prettier.toml` holds the Prettier options.
- `policy.toml` pins the tools, Node, and the packages that ESLint loads.

The bundled `policy.toml` has these lines for `typescript`:

<!-- policy tables: typescript -->
```toml
node = "24.22.3"

[capabilities.typescript.tools.eslint]
configuration = "rules/eslint.toml"
pin = "10.15.0"
reviewed = ["10.15.0"]

[capabilities.typescript.tools.eslint.packages]
"@typescript-eslint/eslint-plugin" = "8.73.0"
"@typescript-eslint/parser" = "8.73.0"
typescript = "6.0.3"

[capabilities.typescript.tools.prettier]
configuration = "rules/prettier.toml"
pin = "3.10.1"
reviewed = ["3.10.1"]

[capabilities.typescript.choices]
exclude = []
```

- `node` sets the Node version for every tool from npm.
- `packages` pins the packages that every reviewed ESLint version installs beside it. A parser or a plugin that `rules/eslint.toml` names must be listed here.
- `gateseal policy add eslint <version> policy` reviews a new ESLint version. The same command works for Prettier.

After you edit `node` or `packages`, run `gateseal policy lock policy`. It records the SHA-256 of each Node archive and checks that the npm registry has each package. See [Policy format](policy-format.md#policy-lock).

## Limits

- npm does not pin the packages that the pinned packages depend on, and it enforces no integrity hash. Your own npm settings, such as `~/.npmrc`, choose the registry.
- ESLint and Prettier skip each file in a `node_modules` folder, also a staged one, as both do without gateseal.
- The hooks get `.ts` and `.tsx` files only. ESLint skips a name in upper case, such as `Widget.TS`, as TypeScript does. pre-commit gives `.mts` and `.cts` files no TypeScript type, so they are not checked.
- No rule needs type information, and this capability runs no type check.
- `gateseal run --json` and `gateseal gate --json` list no ESLint finding by file and line. The hook's entry says that it failed, and the text output shows ESLint's report.
- The ESLint rules can use only the parser and the plugins that the policy's `packages` pin. The ESLint format refuses `extends`, so a preset cannot load. Prettier plugins are not supported: a rule file that sets `plugins`, also in the options of an override, gives `invalid_field`. Pin Prettier 3 or later. Prettier 2 has more options that load code from the repository, such as `pluginSearchDirs` and a parser that it does not know, and a rule file can set them.
- The hooks pass your whole environment to npm, so npm uses your proxy and CA settings. A release binary, such as Betterleaks, gets only a few variables, such as `HOME` and `PATH`.
- A `workspace` setting in your own `.npmrc` stops the first use of a hook, as it stops npm in each folder outside that workspace. With `--preserve-symlinks-main` in `NODE_OPTIONS`, no program that npm installs starts, and so no hook on Node.
- A commit that an npm script starts, such as `npm run commit`, gets the npm settings of that script. npm gives a script the settings of the project's `.npmrc` as `npm_config_` variables, and the first use of a hook passes them to npm. Such a script runs the repository's code already. To keep the project's settings out, run `git commit` yourself.
