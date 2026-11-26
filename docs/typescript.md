# Check TypeScript

The bundled `typescript` capability checks staged `.ts` and `.tsx` files with ESLint and then formats them with Prettier. Both tools run on a Node that the policy pins. So a commit needs no Node or npm on the machine. The checks run at each commit, in `gateseal run`, and in the CI gate.

The bundled `typescript-types` capability checks the project's types with `tsc` at each push and in the CI gate. Select it beside `typescript` or alone. See [Type checks at push](#type-checks-at-push).

## Select the capability

[Install gateseal](install.md), pre-commit and mise first. From the repository root, run:

```sh
gateseal configure --capability hygiene --capability typescript --ci github
pre-commit install
```

In a configured repository, add `"typescript"` to `capabilities` in `gateseal.toml` and run `gateseal configure` instead.

Besides the Manifest, the Lock and `.pre-commit-config.yaml`, configure writes six files under `.gateseal/`:

- `eslint.json`, the ESLint rules.
- `eslint-loader.mjs`, the file that gives the rules to ESLint.
- `eslint-suppressions.json`, an empty list that the hook gives to ESLint in place of the repository's own `eslint-suppressions.json`.
- `prettier.toml`, the Prettier options.
- `prettier-ignore.txt`, an ignore file that lists no file, which the hook gives to Prettier in place of the repository's `.prettierignore` and `.gitignore`.
- `eslint.package-lock.json` and `prettier.package-lock.json`, npm's lockfile of each tool, which `policy.lock` records and the hooks install exactly.

Review the files and commit them together, as [Configure a repository](configure-a-repository.md) shows. To see the versions that the hooks run, run `gateseal status`. Its output includes lines like this:

```text
Tool eslint 10.15.0 for typescript: built-in, npm-package on Node 24.22.3.
```

## What each developer needs

Each developer needs Git, pre-commit 4.7.0 or later, and mise 2026.10.19 or later. Node, npm and gateseal are not needed. The hooks start the Node that the Lock records, whatever Node is on `PATH`. This needs mise's auto-install, which is on by default. With it off, mise starts the `node` on `PATH` until the pinned Node is installed. Each hook then stops and says that mise has no install of the pinned Node, and `gateseal status` warns.

The first commit that stages a TypeScript file downloads these files:

- The pinned Node from nodejs.org. mise installs it and checks its SHA-256 against the Lock.
- ESLint, typescript-eslint's parser and plugin, TypeScript and Prettier from the npm registry. npm installs them once into `gateseal/npm` in your cache folder, outside the repository.

Later commits call no npm and need no network, until a pin changes. The CI gate needs the same network access. Two repositories that pin different Node versions each run their own Node and their own install of the packages on one machine.

## What a commit does

1. ESLint checks each staged TypeScript file and applies the fixes that its rules have, such as `let` to `const`.
2. Prettier formats the same files.

If a hook changes a file, the commit stops. Review the change, stage it and commit again. A finding that ESLint cannot fix also stops the commit. ESLint's report names the file, the line and the rule, such as `@typescript-eslint/no-explicit-any`.

ESLint runs first, so Prettier formats what the fixes leave. The bundled ESLint rules hold no rule about formatting, and they turn off `no-unexpected-multiline`, which conflicts with Prettier. So the two tools do not undo each other's changes.

The bundled rules are the presets `eslint/recommended` and `@typescript-eslint/recommended`. The second preset turns off the core rules that TypeScript checks itself. `policy.lock` records the rules of both presets, so a commit runs no code of a preset. The Prettier options are Prettier's defaults, such as a line width of 80 and double quotes.

## What the repository cannot change

The ESLint and Prettier hooks ignore the repository's own settings and packages:

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

- `rules/eslint.toml` holds the ESLint rules: the presets, and the rules that differ from them. See [the ESLint format](tool-definitions.md#eslint-configuration-format) for what the file can hold.
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

To use other presets, change `presets` in `rules/eslint.toml`, such as to `["eslint/recommended", "@typescript-eslint/strict"]`. To change one rule, set it in `[rules]`. A rule there wins over the presets.

After you edit `node`, `packages` or `presets`, run `gateseal policy lock policy`. It records the SHA-256 of each Node archive, npm's lockfile of each tool, and the rules of each preset. The lockfile lists every package that npm installs, with its integrity hash. See [Policy format](policy-format.md#policy-lock).

## Limits

- ESLint and Prettier skip each file in a `node_modules` folder, also a staged one, as both do without gateseal.
- The hooks get `.ts` and `.tsx` files only. ESLint skips a name in upper case, such as `Widget.TS`, as TypeScript does. pre-commit gives `.mts` and `.cts` files no TypeScript type, so they are not checked.
- No rule needs type information, and this capability runs no type check. `typescript-types` runs one at each push.
- `gateseal run --json` and `gateseal gate --json` list no ESLint finding by file and line. The hook's entry says that it failed, and the text output shows ESLint's report.
- The ESLint rules can use only the parser and the plugins that the policy's `packages` pin. A preset gives only its rules, not its other settings, such as parser options. A preset with a rule that needs type information gives `invalid_field`, such as `@typescript-eslint/recommended-type-checked`. Prettier plugins are not supported: a rule file that sets `plugins`, also in the options of an override, gives `invalid_field`. Pin Prettier 3 or later. Prettier 2 has more options that load code from the repository, such as `pluginSearchDirs` and a parser that it does not know, and a rule file can set them.
- npm reads only the proxy, CA and registry settings of your `.npmrc`: `proxy`, `https-proxy`, `noproxy`, `cafile`, `registry` and a registry's own settings, such as its token. Your `npm_config_` variables can set the first five. A scoped registry, such as `@team:registry`, does not apply.
- A commit that an npm script starts, such as `npm run commit`, gets the proxy, CA and registry settings of that script, such as those of the project's `.npmrc`. To keep them out, run `git commit` yourself.

## Type checks at push

The bundled `typescript-types` capability runs `tsc --noEmit --project tsconfig.json` on the whole project. It reads the project's own `tsconfig.json` at the repository root and the packages that the project's lockfile pins. It runs at `git push` and in the CI gate. It does not run at a commit or in `gateseal run`.

The capability is separate from `typescript`, because a check of the whole project cannot skip paths with `exclude`. The project's `tsconfig.json` chooses the files that `tsc` checks.

### Select the type check

The repository needs `package.json`, `tsconfig.json` and exactly one lockfile at its root: `package-lock.json` for npm, or `pnpm-lock.yaml` for pnpm. From the repository root, run:

```sh
gateseal configure \
  --capability hygiene \
  --capability typescript \
  --capability typescript-types \
  --ci github
pre-commit install
```

In a configured repository, add `"typescript-types"` to `capabilities` in `gateseal.toml` and run `gateseal configure` instead. Commit the changed files. Each clone then needs the push hook. See [Checks at a push](work-in-a-configured-repository.md#checks-at-a-push).

Without `package.json` or a lockfile, `configure` stops with `missing_project`. With both lockfiles, it stops with `ambiguous_project`. It also stops with `ambiguous_project` when `npm-shrinkwrap.json` is beside `package-lock.json`, because npm reads the shrinkwrap in place of the lockfile. `gateseal check` stops the same way when the project changes later. Without `tsconfig.json`, `tsc` stops the push with `error TS5058`.

Each developer needs pre-commit and mise, as for `typescript`. Node, npm, pnpm and gateseal are not needed. pre-commit builds gateseal's hook from gateseal's repository. The first push downloads Node, TypeScript and the project's packages. Later pushes reuse them.

### What a push does

1. gateseal's hook `gateseal-node-packages` installs the packages that the lockfile pins into `node_modules`.
2. `tsc --noEmit --project tsconfig.json` checks the types of the project.

The install is different for each package manager:

- **npm.** `npm ci --dry-run` checks that `package-lock.json` matches the versions in `package.json`. `npm ls --package-lock-only` checks that it matches the source of each dependency, such as a tarball. Then `npm install --no-save` installs only what changed. npm is the one of the pinned Node's install, never an npm on `PATH`. Without that install, the hook stops.
- **pnpm.** `pnpm install --frozen-lockfile` does both in one step. gateseal installs pnpm 10.28.0 once on the pinned Node, checked against its integrity hash, and starts it from that install, outside the repository. If `packageManager` in `package.json` names another pnpm version, pnpm changes to it.

The hook obeys the settings of the project and the user, in `.npmrc`, `pnpm-workspace.yaml` and the environment, as `npm install` or `pnpm install` in a terminal does. It makes this promise:

> A project or user setting can make the hook fail. No setting can make the hook pass with packages that differ from the packages that the lockfile pins.

So a setting such as `omit=dev` leaves out the development dependencies. If `tsc` needs one of them, `tsc` stops the push. No install script of a package or of the project runs, and no pnpmfile. npm sends no audit request, so the registry does not learn the names of private packages. A lockfile that does not match `package.json` stops the push. The checks after the install do not run then, so the push shows one error. Run `npm install` or `pnpm install`, commit the lockfile, and push again. The hook runs these commands:

```sh
npm ci --dry-run --no-usage --ignore-scripts
npm ls --package-lock-only --include=dev --include=optional --include=peer \
  --no-usage
npm install --no-save --package-lock --no-package-lock-only --no-dry-run \
  --no-usage --ignore-scripts --no-audit
pnpm install --frozen-lockfile --lockfile --ignore-scripts --ignore-pnpmfile \
  --no-use-store-server --no-lockfile-only --lockfile-dir=. \
  --config.git-branch-lockfile=false --config.merge-git-branch-lockfiles=false \
  --config.ignore-package-manifest=false --config.fix-lockfile=false \
  --config.dedupe=false --config.update=false --modules-dir=node_modules \
  --config.enable-modules-dir=true --config.symlink=true \
  --config.filter=null --config.filter-prod=null
```

The options turn off only the settings that would make the hook pass with other packages than the lockfile pins. See [the commands that the hook runs](tool-definitions.md#tools-that-read-the-node-project), and [A setting of npm or pnpm stops a push](troubleshooting.md#a-setting-of-npm-or-pnpm-stops-a-push).

A type error also stops the push. `tsc` names the file, the line and the error, such as `src/total.ts(3,46): error TS2322`.

To run both hooks without a push, run this from the repository root:

```sh
pre-commit run --hook-stage pre-push --all-files
```

The CI gate runs the same two hooks in a new clone. `gateseal gate --json` lists the install as a hook entry with the hook `gateseal-node-packages`, the tool `gateseal` and the origin `built-in`. `gateseal status` describes the check like this:

```text
Tool tsc 6.0.3 for typescript-types: built-in, node-project on Node 24.22.3; the repository's lockfile pins what it imports.
```

### Which TypeScript runs

The policy's TypeScript runs, never the `typescript` package of the project. The bundled policy pins TypeScript 6.0.3, the version that its ESLint packages use. The bundled `policy.toml` has these lines for `typescript-types`:

<!-- policy tables: typescript-types -->
```toml
[capabilities.typescript-types.tools.tsc]
pin = "6.0.3"
reviewed = ["6.0.3", "7.0.2", "5.9.3"]
```

TypeScript 6.0.3 reports `error TS5101` for a `tsconfig.json` option that TypeScript 7 removed, such as `baseUrl`. Replace the option, or set `"ignoreDeprecations": "6.0"` in `compilerOptions`. TypeScript 5.9.3 accepts these options.

To select another reviewed version, add a [version exception](policy-contract.md#version-exceptions) to `gateseal.toml`. TypeScript 7.0.2 is the faster native compiler, but it reports `error TS5102` for the removed options and has no setting that allows them. This exception selects it:

<!-- manifest tables: tsc exception -->
```toml
[exceptions.tsc]
version = "7.0.2"
reason = "Check types with the faster native compiler."
review_by = 2027-03-01
```

Run `gateseal configure` and commit the changed files. A team policy reviews another version with `gateseal policy add tsc <version> policy`.

### What the type check cannot do

- No install script runs. Some projects get types from an install or generate step, such as a `postinstall` code generator. `tsc` then reports missing types. Commit the generated files, so that each push and the CI gate find them.
- No pnpmfile runs: not `.pnpmfile.cjs`, not one that a setting names, and not one from a config dependency. pnpm records a pnpmfile's checksum in `pnpm-lock.yaml`. So when the lockfile was written with a pnpmfile, the install stops with `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH`. Move what the pnpmfile changes into `overrides` or `packageExtensions` in `pnpm-workspace.yaml`. Then delete the pnpmfile, run `pnpm install` and commit the lockfile.
- Your npm and pnpm settings apply, such as `omit=dev` or npm's `legacy-peer-deps`. With `legacy-peer-deps`, npm installs no package that only a peer dependency asks for, even when the lockfile pins it. So write the lockfile with the settings that the push uses.
- pnpm keeps a folder at the top of `node_modules` that it did not make, such as a package from an earlier npm install or from an install with `node-linker=hoisted`. `tsc` can then find a package that `package.json` does not name, so a push can pass that the CI gate stops. After a change of the package manager or of `node-linker`, delete `node_modules` one time.
- The generated CI file gives npm and pnpm no registry token. So the CI gate can install only packages that need no login.
- The built-in `tsc` takes its options from the project's `tsconfig.json`, so a project can turn strict checks off. A team policy can own them with [its own tool and strict flags](add-a-tool.md#check-a-second-tsconfig-with-strict-tsc-flags).
- Yarn is not supported. Neither are workspaces nor project references. The check reads one lockfile and one `tsconfig.json`, both at the repository root.
- `gateseal gate --json` lists no `tsc` error by file and line. The hook's entry says that it failed, and the text output shows the report of `tsc`.

## gateseal and pre-commit's Node hooks

Without gateseal, a repository can run ESLint and Prettier as pre-commit hooks with `language: node`. pre-commit then installs Node and the packages into an environment of each hook. The table compares the two setups. Its last five rows show where pre-commit is equal or better.

| | gateseal | pre-commit `language: node` |
| --- | --- | --- |
| What you write for ESLint and Prettier | One capability name in `gateseal.toml` | A hook block for each tool, with its version and packages, and each tool's own configuration file |
| Node version | The policy's pin | The hook's `language_version`. Without one, the Node on `PATH`, or else the latest release |
| Checksum of Node | SHA-256 in the Lock | None |
| Copies of Node | One for each Node version | One for each hook and Node version |
| Lock of the tool's packages | npm's lockfile for each tool version | None. npm resolves the packages again at each install |
| Integrity of every package | Checked against the lockfile | Not pinned |
| Install scripts of packages | Never run | Run |
| Your npm settings at the tool's install | Only proxy, CA and registry settings | All. For example, `workspace` in `~/.npmrc` stops the install |
| Packages that `tsc` reads | The packages that the project's lockfile pins, installed at each push. A setting can make the push fail, but none can make it pass with other packages. After a change of package manager, delete `node_modules` once, as [What the type check cannot do](#what-the-type-check-cannot-do) says | The packages in `node_modules`. Nothing installs them, so `tsc` fails in a new clone |
| Later commits | A little slower, because gateseal's hook starts before each tool | A little faster |
| ESLint plugins | Each plugin that the policy pins | Each plugin in `additional_dependencies`. An ES module configuration, such as `eslint.config.mjs`, cannot import it, so the project installs it too |
| ESLint presets | `eslint/recommended` and each preset of a pinned plugin, with its rules only, and none with rules that need type information | Each preset that the ESLint configuration loads, with all its settings |
| ESLint rules that need type information | None | Possible, when `node_modules` holds the project's packages |
| Hook entry and name of a failed hook | One short line. A failed hook shows `hook id: gateseal-npm-tool` below the tool's name, and `SKIP=prettier` still works | One short line. A failed hook shows `hook id: prettier` |
