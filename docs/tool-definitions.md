# Tool definitions

A policy can describe a tool in `tools/<name>.toml`. The definition gives the tool's source and hooks. Its filename sets the tool name, which must match `[a-z][a-z0-9-]*`. List that name under a capability in `policy.toml`. See [Add a tool](add-a-tool.md) for complete definitions.

A team definition cannot reuse the name of a built-in definition, or the reserved names `pre-commit`, `python` and `node`. The built-in definitions are `pre-commit-hooks`, `ruff`, `pyright`, `coverage`, `crap`, `pytestmut`, `uv-lock`, `uv-audit`, `deptry`, `betterleaks`, `osv-scanner`, `prettier`, `eslint`, `eslint-typed`, `tsc`, `knip`, `vitest`, `jest`, `crap-typescript` and `stryker`.

## Shared fields

| Field | Value |
| --- | --- |
| `kind` | `pre-commit-repository`, `release-binary`, `uv-project`, `npm-package` or `node-project`. See [Kinds](#kinds). |
| `stages` | `["commit", "run", "ci"]` runs the hooks at commit, in `gateseal run` and in the CI gate. `["push", "ci"]` runs them at `git push` and in the CI gate. `["ci"]` runs them in the CI gate alone. |
| `scope` | `files` or `project`. With `files`, pre-commit passes matching filenames. With `project`, the hook gets no filenames and checks the whole project. |
| `configuration.format` | Optional `toml` or `eslint`. The generated file is `.gateseal/<name>.toml` or, for `eslint`, `.gateseal/<name>.json`. |
| `files.<key>` | Optional fixed file with lowercase `extension` and text `content` using LF line endings. |
| `hooks.<id>` | At least one hook. See [Hooks](#hooks). |
| `history.args` | Optional arguments of the tool's executable for a CI history scan, for a `release-binary`, `uv-project` or `npm-package` tool. Include `{range}` exactly once. |
| `choices.<name>` | Optional [choice](#choices) in the tool configuration. It needs a `toml` configuration. |
| `network` | Optional `true` for a tool that asks a service on the network. A `release-binary` tool then also gets the proxy and certificate variables. Defaults to `false`. |

An unsupported kind or value gives `unsupported_tool_definition`. So does a kind, stage or format that an older gateseal release lacks. Set the policy's `minimum_compiler` to the first release that has it. Unknown fields are invalid.

Use the push stage for a tool that is too slow for each commit. Each clone then needs the push hook. See [Checks at a push](work-in-a-configured-repository.md#checks-at-a-push).

A tool with the `project` scope reads the working tree, so a capability with such a tool cannot expose `exclude`. Its hook runs at every commit or push of its stage, also when the change only deletes files. With `types_or`, the hook runs only when a changed file has one of those types.

## Hooks

| Field | Value |
| --- | --- |
| `name` | Required. The name that pre-commit shows. Hook IDs and names are distinct across the policy's tools. |
| `output` | Required. `concise` for `file:line:column: rule message` findings. `plain` for pass, fail, or modified results without structured findings. |
| `args` | Optional literal arguments, passed without a shell. |
| `types_or` | Optional file types, as pre-commit names them. |
| `exclude` | Optional Python regular expression without a flag for the whole expression, such as `(?i)`. The hook never gets a file whose path matches it, such as `'(^|/)tests?/'`. |
| `reads`, `writes` | Optional [reports](#reports) that the hook reads or writes. |
| `finding_exit_codes` | Optional distinct exit codes from 1 to 255 with which the tool reports a finding, such as `[1]`. |

In `args`, use `{configuration}` or `{<files key>}` as a whole argument to pass a generated file, and `{<report name>}` to pass a report. Other braces are invalid.

By default, every failure of a hook is a failed check, which exits 1. Some tools also fail when they cannot check, such as when they cannot reach a service. With `finding_exit_codes`, a failure with another exit code makes the hook `stopped`. `gateseal run` and the CI gate then report `tool_failed` and exit 4. For example, the bundled `uv-audit` hook, shortened:

```toml
[hooks.uv-audit]
name = "uv audit"
args = ["--frozen", "--config-file", "{configuration}"]
output = "plain"
finding_exit_codes = [1]
```

gateseal's own hooks `gateseal-check`, `gateseal-lfs-check`, `gateseal-node-packages` and `gateseal-npm-tool` keep their IDs and names. A hook with one of them gives `duplicate_identifier`.

## Reports

A hook can leave a file for a later hook, such as coverage data for a hook that scores it. The writing hook lists the report's name in `writes`. The reading hook lists it in `reads`:

```toml
[hooks.coverage-run]
name = "tests with coverage"
output = "plain"
args = ["run", "--data-file", "{coverage.data}", "-m", "pytest"]
types_or = ["python", "pyi"]
writes = ["coverage.data"]

[hooks.coverage-json]
name = "coverage report"
output = "plain"
args = ["json", "--data-file", "{coverage.data}", "-o", "{coverage.json}"]
types_or = ["python", "pyi"]
reads = ["coverage.data"]
writes = ["coverage.json"]
```

- A report name matches `[a-z][a-z0-9-]*\.[a-z0-9]+`, such as `coverage.json`. Its file is `.gateseal/reports/<name>`, which Git ignores.
- An earlier hook must write each report that a hook reads, in a tool whose `stages` include every stage of the reading hook's tool. Hooks run in the order of the policy's capabilities, then tools, then hooks. Otherwise the policy stops with `missing_report`. So does `gateseal configure`, when a Manifest selects the reading hook without the writing one.
- Give a reading hook the `types_or` of the hook that writes its report. Otherwise it can run when the writing hook does not.
- `gateseal gate` deletes the reports of an earlier run before it starts the hooks. A commit or push through pre-commit alone does not. So a tool that adds to an earlier report, such as coverage.py with parallel data files, needs a hook that deletes it first.
- If a hook ran in the gate without a report that it reads, the gate exits 2 with `missing_report`. When the writing hook failed, the gate fails as a failed check instead.

## Kinds

### Hook repositories

A `pre-commit-repository` source has an HTTPS `repository` and a `tag` pattern that contains `{version}`. The URL has no credentials, query, or trailing slash. The generated `policy.lock` records the upstream commit behind each reviewed tag. A Python hook uses pre-commit's Python. Two tools can take different hooks from the same hook repository, as `uv-lock` and `uv-audit` do.

### Release binaries

A `release-binary` source also has `archive`, `checksums`, and `platforms`. The archive pattern contains `{platform}` and may contain `{version}`. The checksum filename may contain `{version}`. Map at least one of `linux-x64` and `macos-arm64` to its upstream platform name. Set `executable.name` to the bare program name.

mise checks the archive's recorded SHA-256 when it downloads the archive. With `executable.check_at_commit = true`, the policy lock also records the program's SHA-256, and the commit hook checks the installed copy. `gateseal run` and the CI gate do not make this check.

A release binary gets only the environment variables that mise keeps, such as `HOME` and `PATH`. A hook of any other kind gets the caller's environment. gateseal does not review what a tool does with its arguments. For example, a scanner may read a repository's ignore file.

### Tools in the uv project

A `uv-project` tool runs in the repository's own uv project, so it can import the project's packages. Its source has a `package`: the tool's name on PyPI, with any extras of the tool in brackets, such as `pyright[nodejs]`. Set `executable.name` to the program that the package installs. `check_at_commit` is invalid for this kind.

The source can also list the project's `extras` and dependency `groups` that the tool needs. `default_groups = false` leaves out the project's default groups:

```toml
[source]
package = "mypy"
extras = ["cli"]
groups = ["typing"]
```

- **Environment.** Each tool has its own environment, `.gateseal/environments/<name>`, which Git ignores. At each run, uv installs exactly what the repository's `uv.lock` pins, with the default dependency groups. The hook does not read or change `.venv`.
- **Project.** The repository needs `pyproject.toml` and a regular `uv.lock` file at its root. Otherwise `gateseal configure` and `gateseal check` stop with `missing_project`. The hook stops when `uv.lock` is out of date. gateseal does not detect a uv workspace in a folder above the repository, whose `uv.lock` uv would use.
- **A package with the tool's name.** `uv.lock` can install a package with the tool's name and pinned version from the project's own source. uv would then run that package in the tool's place. So the commit check, and every command that reads the Lock, stop with `shadowed_tool`.
- **An analyzer.** The tool's Python finds the tool's own dependencies before the project's packages. So make an analyzer read the tool's environment by name, as the [mypy example](add-a-tool.md#add-mypy-a-tool-that-imports-the-project) does with `--python-executable`.

### Tools from npm

An `npm-package` source has a `package`: the tool's name on the npm registry, without a version, such as `prettier` or `@biomejs/biome`. Set `executable.name` to the program that the package installs. The program must be a JavaScript file, because the hook starts the pinned Node with it. `check_at_commit` is invalid for this kind.

The policy pins the Node version in `node`, and can pin more packages in the tool's `packages`. See [Policy format](policy-format.md#policy-fields). `gateseal policy lock` runs npm on that Node to lock them, so it needs mise and the npm registry.

Each hook is gateseal's own hook `gateseal-npm-tool`, with the tool's hook ID as its `alias`. So `SKIP=tsc` and `pre-commit run tsc --hook-stage pre-push` name the hook by its own ID. When the hook fails, pre-commit prints `hook id: gateseal-npm-tool` below the hook's name. The hook exits 127 when it cannot install or start the tool, so list no 127 in `finding_exit_codes`.

The kind guarantees these things, whatever a definition says:

- **Node.** The hook runs the pinned Node, whatever Node is on `PATH`. mise installs it at the first use and checks its SHA-256 against the Lock.
- **Packages.** npm installs exactly the packages that `policy.lock` records, each checked against its integrity hash. A changed hash or a missing package stops the hook.
- **Install scripts.** No install script of a package runs.
- **npm settings.** npm reads only these settings of your `.npmrc`: `proxy`, `https-proxy`, `noproxy`, `cafile`, `registry` and a registry's own settings, such as its token.
- **Generated files.** The hook never gets a file that gateseal writes. A tool with the `project` scope reads the working tree itself, so its own arguments must keep it away from these files.

The installs are in `gateseal/npm` in your cache folder. To free the space, delete that folder. A hook trusts an install that it finds there, so keep the cache folder outside each repository whose files you do not trust.

### Tools that read the Node project

A `node-project` tool is an `npm-package` tool that also reads the packages of the repository's own Node project. A type checker such as `tsc` needs them to resolve imports. The built-in `tsc`, `eslint-typed`, `knip`, `vitest`, `jest` and `stryker` are of this kind. The policy's version of the tool runs, not a version that the project's lockfile pins. Jest is the exception: its launcher starts the Jest that the project installs.

gateseal's own hook `gateseal-node-packages` runs before the tool's hooks, in each stage that runs such a tool. It installs what the project's lockfile pins into `node_modules`. When the install fails, pre-commit runs no later hook of that stage. It runs no install script. An npm or pnpm setting can make the install fail, but none can make it pass with other packages than the lockfile pins.

The repository needs `package.json` and exactly one of `package-lock.json` and `pnpm-lock.yaml` at its root. Otherwise `gateseal configure` and `gateseal check` stop:

| Code | Meaning |
| --- | --- |
| `missing_project` | The repository root has no `package.json`, or neither lockfile. |
| `ambiguous_project` | The repository root has both lockfiles. Or it has `npm-shrinkwrap.json` beside `package-lock.json`, and npm reads the shrinkwrap in place of the lockfile. |

## ESLint configuration format

A definition with `configuration.format = "eslint"` takes ESLint's data from a TOML rule file. `gateseal configure` checks the file and writes it as JSON to `.gateseal/<name>.json`. The rule file holds exactly these keys:

| Key | Value |
| --- | --- |
| `files` | Required. One or more patterns of the files that ESLint checks, from the repository root, such as `"**/*.ts"`. |
| `parser` | Optional. An npm package that the tool's `packages` pin, such as `@typescript-eslint/parser`. |
| `plugins` | Optional table. Maps each rule prefix, such as `@typescript-eslint` or `react`, to an npm package that the tool's `packages` pin. |
| `presets` | Optional. A list of [presets](#presets) whose rules apply first, in order. |
| `rules` | Optional table. Maps each rule to `off`, `warn` or `error`, or to an array of the severity and then the rule's options. A rule named `<prefix>/<rule>` needs its prefix in `plugins`. A rule here wins over a preset. |

Any other key gives `invalid_field`, such as `extends`, `languageOptions` or `settings`. So no key loads another file. A definition of this format takes no choices.

The generated JSON file is data, not an ESLint configuration. A loader file gives it to ESLint with `--config`, so ESLint runs no configuration file of the repository. The hook also passes an empty file with `--suppressions-location`, so ESLint does not read the repository's `eslint-suppressions.json`. A team definition of this format needs both, so copy [the built-in definition](https://github.com/karstenevers/gateseal/blob/main/src/gateseal/data/tools/eslint.tool.toml). It needs ESLint 9.24.0 or later.

### Presets

A preset is `eslint/recommended`, ESLint's recommended rules, or `<prefix>/<name>`, a config of the plugin that `plugins` declares for the prefix. An example is `@typescript-eslint/strict`.

`gateseal policy lock` reads each preset once and records its rules in `policy.lock`. So a commit gets the rules as data and runs no code of a preset. After a plugin upgrade, the diff of `policy.lock` shows each rule that changed.

A preset gives only its rules, and the files that they apply to. Its other settings, such as parser options, do not apply. Each of these presets gives `invalid_field`:

- A preset that the package does not have. The message lists the package's presets.
- A preset of a plugin that `packages` do not pin.
- A preset that turns on a rule of a plugin that `plugins` does not declare.
- For an `npm-package` tool, a preset that turns on a rule that needs type information. The message names the rule.

### Rules that need type information

The built-in `eslint-typed` definition runs ESLint as a `node-project` tool, at a push and in the CI gate. Its rule file holds the same keys, and its presets and rules can need type information.

- Its loader reads `tsconfig.json` at the repository root with the TypeScript that the tool's `packages` pin.
- ESLint checks only the files that `tsconfig.json` includes and that `files` names.
- The loader stops with TypeScript's message when it cannot read `tsconfig.json`, or when `tsconfig.json` includes no file.
- The hook gets no file names, and applies no fixes.

A team definition of a typed ESLint copies [the built-in definition](https://github.com/karstenevers/gateseal/blob/main/src/gateseal/data/tools/eslint-typed.tool.toml), loader included. With the loader of `eslint`, ESLint stops with `You have used a rule which requires type information`.

## Choices

A choice names a top-level configuration `key` and a `since` map. The map gives each accepted value and the earliest tool release that supports it. Values use lowercase letters and digits. Expose the choice and its default in `[capabilities.<name>.choices]` in `policy.toml`. The reserved keys `extend` and `plugins` are invalid, because each loads a file or a package in some tool.
