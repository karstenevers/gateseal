# Tool definitions

A policy can describe a tool in `tools/<name>.toml`. The definition gives its source and hooks. Its filename sets the tool name, which must match `[a-z][a-z0-9-]*`. List that name under a capability in `policy.toml`.

For example, this source fragment describes the Black hook repository:

```toml
[source]
repository = "https://github.com/psf/black-pre-commit-mirror"
tag = "{version}"
```

See [Add a tool](add-a-tool.md) for complete definitions. Built-in definitions exist for `pre-commit-hooks`, `ruff`, `pyright`, `coverage`, `crap`, `pytestmut`, `uv-lock`, `uv-audit`, `deptry`, `betterleaks`, `prettier`, `eslint`, and `tsc`. A team definition cannot reuse those names or the reserved names `pre-commit`, `python`, and `node`.

## Shared fields

| Field | Value |
| --- | --- |
| `kind` | `pre-commit-repository`, `release-binary`, `uv-project`, `npm-package` or `node-project`. |
| `stages` | `["commit", "run", "ci"]`, `["push", "ci"]`, or `["ci"]`. |
| `scope` | `files` or `project`. With `files`, pre-commit passes matching filenames. With `project`, the hook gets no filenames and checks the whole project. It runs at every commit, or at every push in the push stage, unless it lists `types_or`. |
| `configuration.format` | Optional `toml` or `eslint`. The generated file is `.gateseal/<name>.toml` or, for `eslint`, `.gateseal/<name>.json`. |
| `files.<key>` | Optional fixed file with lowercase `extension` and text `content` using LF line endings. |
| `hooks.<id>` | At least one hook. |
| `history.args` | Optional arguments of the tool's executable for a CI history scan, for a `release-binary`, `uv-project` or `npm-package` tool. Include `{range}` exactly once. |
| `choices.<name>` | Optional choice in the tool configuration. It needs a `toml` configuration. |

An unsupported kind or value gives `unsupported_tool_definition`. Unknown fields are invalid.

## Stages

| `stages` | The hooks run |
| --- | --- |
| `["commit", "run", "ci"]` | At commit, in `gateseal run`, and in the CI gate. |
| `["push", "ci"]` | At `git push` and in the CI gate. |
| `["ci"]` | In the CI gate alone. |

Use the push stage for a tool that is too slow for each commit. Each clone then needs the push hook. See [Checks at a push](work-in-a-configured-repository.md#checks-at-a-push). A gateseal release without the push stage gives `unsupported_tool_definition` for such a policy. Set the policy's `minimum_compiler` to the first release that has the stage.

## Hooks

Each hook has a distinct `name`, optional literal `args`, optional `types_or`, optional `reads` and `writes`, optional `finding_exit_codes`, and `output`. gateseal's own hooks `gateseal-check`, `gateseal-lfs-check` and `gateseal-node-packages` keep their IDs and names. A hook with one of them gives `duplicate_identifier`. A hook of a tool with the `project` scope that lists `types_or` runs only when a changed file has one of those types. Use `concise` for `file:line:column: rule message` findings. Use `plain` for pass, fail, or modified results without structured findings. Use `{configuration}` or `{<files key>}` as a whole argument to pass a generated file, and `{<report name>}` to pass a [report](#reports). Other braces are invalid. Arguments are passed without a shell.

By default, every failure of a hook is a failed check, which exits 1. Some tools also fail when they cannot check, such as when they cannot reach a service. List the exit codes with which the tool reports a finding in `finding_exit_codes`, such as `[1]`. When the hook fails with another exit code, the hook is `stopped`, and `gateseal run` and the CI gate report `tool_failed` and exit 4. The list holds one or more distinct integers from 1 to 255. For example, the bundled `uv-audit` hook, shortened:

```toml
[hooks.uv-audit]
name = "uv audit"
args = ["--frozen", "--config-file", "{configuration}"]
output = "plain"
finding_exit_codes = [1]
```

Two tools can take different hooks from the same hook repository, as `uv-lock` and `uv-audit` do. Hook IDs and names stay distinct across the policy's tools.

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

- A report name matches `[a-z][a-z0-9-]*\.[a-z0-9]+`, such as `coverage.json`. Its file is `.gateseal/reports/<name>`.
- Use `{<report name>}` as a whole argument to pass that path. A hook can name only a report that it reads or writes. It lists each name once, and it cannot read a report that it writes.
- An earlier hook must write each report that a hook reads, in a tool whose `stages` include every stage of the reading hook's tool. Hooks run in the order of the policy's capabilities, then tools, then hooks. Otherwise the policy stops with `missing_report`. `gateseal configure` stops with the same code when a Manifest selects the reading hook without the writing one.
- A repository with a hook that writes a report gets the generated `.gateseal/reports/.gitignore`, so Git ignores every report.
- `gateseal gate` deletes the reports of an earlier run before it starts the hooks. If a hook ran without a report that it reads, the gate exits 2 with `missing_report`. When the writing hook failed, the gate fails as a failed check instead. A commit or push through pre-commit alone does neither. So a tool that adds to a report of an earlier run, such as coverage.py with parallel data files, needs a hook that deletes it first.
- Give a reading hook the `types_or` of the hook that writes its report. Otherwise it can run when the writing hook does not.
- `gateseal check`, `status` and `doctor` name each report and the hook that writes it.

## Sources and executables

A `pre-commit-repository` source has an HTTPS `repository` and a `tag` pattern containing `{version}`. The URL has no credentials, query, or trailing slash. The generated `policy.lock` records the upstream commit behind each reviewed tag. A Python hook uses pre-commit's Python.

A `release-binary` source also has `archive`, `checksums`, and `platforms`. The archive pattern contains `{platform}` and may contain `{version}`. The checksum filename may contain `{version}`. Map at least one of `linux-x64` and `macos-arm64` to its upstream platform name. Set `executable.name` to the bare program name.

The optional `executable.check_at_commit` defaults to `false`. When true, the policy lock records the program's SHA-256 and the commit hook checks an installed copy. The check does not run in `gateseal run` or the CI gate. mise verifies an archive's recorded SHA-256 when it downloads the archive.

A `uv-project` source has a `package`: the tool's name on PyPI, with any extras of the tool in brackets, such as `pyright[nodejs]`. Set `executable.name` to the program that the package installs. The source can also list the project's `extras` and dependency `groups` that the tool needs. `default_groups = false` leaves out the project's default groups:

```toml
[source]
package = "mypy"
extras = ["cli"]
groups = ["typing"]
```

The hook runs one command in the repository's uv project:

```sh
env UV_PROJECT_ENVIRONMENT=.gateseal/environments/mypy uv run --locked --exact --all-packages --extra cli --group typing --with mypy==2.4.1 -- mypy
```

- Each tool has its own environment, `.gateseal/environments/<name>`, which Git ignores. At each run, uv installs exactly what the repository's `uv.lock` pins and removes every other package. So a commit and the CI gate read the same packages. The hook does not read or change `.venv`. A package that you install there yourself, such as from an extra, does not reach the tool.
- uv installs each package's dependencies and the project's default dependency groups, which is `dev` unless `tool.uv.default-groups` names others. It adds each extra and group that the source lists. In a uv workspace, it installs the packages of every member. uv stops the hook when the project defines no extra or group of a listed name.
- The hook stops when `uv.lock` is missing or out of date. The tool runs on the project's Python. The first run installs the packages, which needs the network.
- The policy pins the tool by version, without a wheel hash. `gateseal policy lock` checks that PyPI has each reviewed release. The project's uv settings choose the index.
- `uv.lock` can install a package with the tool's name and pinned version from the project's own source, such as a path, a Git repository or the packaged project itself. uv would then run that package in the tool's place. So the commit check, and every command that reads the Lock, stop with `shadowed_tool`. They stop for such a package anywhere in `uv.lock`, also in an extra or group that the tool does not install.
- That Python finds the tool's own dependencies before the project's packages. So make an analyzer read the tool's environment by name. The bundled Pyright rules set `venvPath` and `venv`, and the [mypy example](add-a-tool.md#add-mypy-a-tool-that-imports-the-project) passes `--python-executable`.
- `check_at_commit` is invalid for this kind.
- A tool that imports the project's packages itself needs a uv cache whose path holds no space. With a space, uv starts the program outside the project. This was seen with uv 0.13.6 and 0.13.9 on Linux, and is [uv issue 23299](https://github.com/astral-sh/uv/issues/23299). The bundled deptry definition avoids it: its program is `python`, and its arguments start with `-m deptry`.

The repository needs `pyproject.toml` and `uv.lock` at its root, with `uv.lock` as a regular file and not a link. `pyproject.toml` must not set `tool.uv.managed` to `false`. Otherwise `gateseal configure` and `gateseal check` stop with `missing_project`. The commit check stops a commit that does not hold `uv.lock` as a regular TOML file. It does not check `pyproject.toml`: without it, uv starts the tool outside a project. The repository must also be the root of its uv project. If a folder above it holds a uv workspace that lists the repository as a member, uv uses that workspace's `uv.lock`, and gateseal does not detect it.

A hook with the `project` scope and no `types_or` runs at every commit and gate run, also when the change only deletes files. In the push stage, it runs at every push and gate run. With `types_or`, it runs only when a changed file has one of those types. A change that only deletes such files does not start it, because pre-commit matches no hook against a deleted file. A capability with such a tool cannot expose `exclude`, because the tool reads every path. The tool reads the working tree. At a commit, pre-commit hides the unstaged changes of tracked files, and untracked files stay. So a file that Git does not track can change the result of a commit. At a push, pre-commit hides nothing, so the tool reads the checked-out files, also when the push names another branch. The CI gate checks a new clone.

Release binaries run through `mise --no-config exec --deny-env`. A hook of any other kind receives the caller's environment. uv then reads the caller's `UV_` settings, and a setting such as `UV_NO_PROJECT` changes what the hook does. A `uv-project` hook sets `UV_PROJECT_ENVIRONMENT` itself. The program reads pre-commit's variables, such as `PRE_COMMIT_FROM_REF` and `PRE_COMMIT_TO_REF` in a CI gate run on a range. Review a tool's arguments and behavior separately. For example, a scanner may read a repository's ignore file.

## Tools from npm

An `npm-package` source has a `package`: the tool's name on the npm registry, without a version. Examples are `prettier` and `@biomejs/biome`. Set `executable.name` to the program that the package installs. The program must be a JavaScript file, because the hook starts the pinned Node with it. `check_at_commit` is invalid for this kind.

The policy pins the Node version in `node`, and can pin more packages in the tool's `packages`. See [Policy format](policy-format.md#policy-fields). Each hook runs one command, in this form:

```text
mise --no-config exec <Node request> -- /bin/sh -c '<script>' <program> <args>
```

The script installs the packages once into a folder of your cache folder, then starts the program on the pinned Node. When it cannot install or start the program, the hook exits 127, so list no 127 in `finding_exit_codes`.

The kind guarantees these things, whatever a definition says:

- The hook runs the pinned Node, whatever Node is on `PATH` or in the repository's `node_modules`. If mise's auto-install is off and the pinned Node is missing, the hook stops. Two repositories with different pins each run their own Node on one machine.
- npm installs exactly the listed versions. `gateseal policy lock` checks that the npm registry has each one.
- Each machine type, each Node version and each set of pins has its own install. A repository with another Node pin never runs your packages on its Node.
- The program is the one of the tool's own package, never a program of the same name from another package.
- No install script of a package runs.
- The hook never gets a file that gateseal writes. Its `exclude` pattern covers `.gateseal/`, `.pre-commit-config.yaml`, `gateseal.toml`, `gateseal.lock` and the file of each CI provider. A capability's `exclude` choice adds its paths. A tool with the `project` scope gets no file names and reads the working tree itself, so its own arguments must keep it away from these files.

The first use of a hook downloads Node and the packages, which needs network access. So does the first use after a change of a pin. Later uses call no npm and ask the registry nothing. gateseal does not remove an old install. To free the space, delete `gateseal/npm` in your cache folder. The next use of each hook installs again. A hook trusts an install that it finds in the cache folder, as pre-commit trusts its own folder there. So keep the cache folder outside each repository whose files you do not trust. The kind does not pin what the listed packages depend on, and passes the caller's environment to npm. See [the limits](typescript.md#limits).

A gateseal release without the `npm-package` kind or the `eslint` format below gives `unsupported_tool_definition` for such a policy. Set the policy's `minimum_compiler` to the first release that has them.

## Tools that read the Node project

A `node-project` tool is an `npm-package` tool that also reads the packages of the repository's own Node project. A type checker such as `tsc` needs them to resolve imports. The built-in `tsc` is of this kind. Its `[source]` and its `packages` table are those of an `npm-package` tool. So are its records in the Policy Lock and the Lock. Each hook starts the tool from gateseal's own install on the pinned Node, as an `npm-package` hook does. So the policy's version of the tool runs, never a version that the project's lockfile pins.

The repository needs `package.json` and exactly one of `package-lock.json` and `pnpm-lock.yaml` at its root. gateseal's own hook `gateseal-node-packages` runs before the tool's hooks, in each stage that runs such a tool. It installs what the lockfile pins into `node_modules`:

- **npm.** `npm ci --dry-run` checks that the lockfile matches the versions in `package.json`. `npm ls --package-lock-only` checks that it matches the source of each dependency, such as a tarball. Then `npm install --no-save` installs only what changed. The hook runs the npm of the pinned Node's install, with that install's folder first on `PATH`. Without that install, the hook stops with status 1.
- **pnpm.** `pnpm install --frozen-lockfile` does both. gateseal installs pnpm 10.28.0 once on the pinned Node and starts it from that install, as it starts an `npm-package` tool. If `packageManager` in `package.json` names another pnpm version, pnpm changes to it.

The hook runs these commands:

```sh
npm ci --dry-run --no-global --location=project \
  --no-usage --ignore-scripts --no-audit --no-fund
npm ls --package-lock-only --package-lock --no-global --location=project \
  --include=dev --include=optional --include=peer \
  --no-usage --ignore-scripts --no-audit --no-fund
npm install --no-save --package-lock --no-package-lock-only --no-dry-run \
  --no-global --location=project --include=dev --include=optional --include=peer \
  --no-usage --ignore-scripts --no-audit --no-fund
pnpm install --frozen-lockfile --ignore-scripts --ignore-pnpmfile \
  --no-use-store-server --no-prod --no-dev --no-only --optional \
  --lockfile --no-lockfile-only --lockfile-dir=. \
  --config.git-branch-lockfile=false --config.merge-git-branch-lockfiles=false \
  --config.ignore-package-manifest=false --config.resolution-only=false \
  --config.fix-lockfile=false --config.dedupe=false --config.update=false \
  --modules-dir=node_modules --package-import-method=auto \
  --config.enable-modules-dir=true --config.confirm-modules-purge=false \
  --config.symlink=true --config.node-version=null \
  --config.filter=null --config.filter-prod=null
```

An option on the command line wins over `.npmrc`, `pnpm-workspace.yaml` and the environment. The hook overrides only the settings that would make the install differ from the lockfile's: whether npm or pnpm reads the lockfile and which file it reads, which kinds of dependency it installs, whether it installs at all, whether it installs into the project or into the global folder of Node, where `node_modules` is, and which code runs. Every other setting of the project and of the user applies, such as the registry, the login, hoisting and `legacy-peer-deps`, because `tsc` must read the packages as the project's own install lays them out. `pnpm install` has no option for some settings, so `--config.<setting>` sets them, and `null` restores the default. pnpm reads only `pnpm-lock.yaml`, never a lockfile of a git branch. If `node_modules` holds an install with other settings, such as an install without development dependencies, pnpm recreates `node_modules` and asks no question. npm also gets `npm_config_git=git` and `npm_config_node_options=null` in its environment. For a git dependency with a script, npm runs npm again in a clone of the dependency, and only the environment reaches that npm. So git comes from `PATH`, Node gets no options, and no `.npmrc` names a program to run. No install script and no pnpmfile runs. A lockfile that does not match `package.json` stops the hook. Its `Next:` line names the install to run and the lockfile to commit. When the install fails, pre-commit runs no later hook of that stage, because a tool gives a wrong result without the packages.

| Code | Meaning |
| --- | --- |
| `missing_project` | The repository root has no `package.json`, or neither lockfile. Only npm and pnpm projects are supported. |
| `ambiguous_project` | The repository root has both lockfiles, so the package manager is not clear. Or it has `npm-shrinkwrap.json` beside `package-lock.json`, and npm reads `npm-shrinkwrap.json` in place of the lockfile. |

`gateseal configure` and `gateseal check` give these codes. A push through pre-commit alone has no such check. The install hook then stops and names what the project lacks. Yarn, workspaces and project references are not supported. See [Type checks at push](typescript.md#type-checks-at-push) for the other limits.

A gateseal release without the `node-project` kind gives `unsupported_tool_definition` for such a policy. Set the policy's `minimum_compiler` to the first release that has it.

## ESLint configuration format

A definition with `configuration.format = "eslint"` takes ESLint's data from a TOML rule file. `gateseal configure` checks the file and writes it as JSON to `.gateseal/<name>.json`. The rule file holds exactly these keys:

| Key | Value |
| --- | --- |
| `files` | Required. One or more patterns of the files that ESLint checks, from the repository root, such as `"**/*.ts"`. |
| `parser` | Optional. An npm package that the tool's `packages` pin, such as `@typescript-eslint/parser`. |
| `plugins` | Optional table. Maps each rule prefix, such as `@typescript-eslint`, `react` or `@next/next`, to an npm package that the tool's `packages` pin. |
| `rules` | Required table. Maps each rule to `off`, `warn` or `error`, or to an array of the severity and then the rule's options. A rule named `<prefix>/<rule>` needs its prefix in `plugins`. |

Any other key gives `invalid_field`, such as `extends`, `languageOptions` or `settings`. So no key loads another file, names a preset, or gives type information. A value that JSON cannot hold, such as a TOML date or an infinite number, gives `invalid_field` too. A definition of this format takes no choices.

The JSON file is data, not an ESLint configuration. The built-in `eslint` definition adds a loader as the fixed file `files.loader`, written as `.gateseal/eslint-loader.mjs`. Its hook passes the loader with `--config {loader}`, so ESLint runs no configuration file of the repository. The definition also adds an empty list of suppressions as the fixed file `files.suppressions`, written as `.gateseal/eslint-suppressions.json`, and its hook passes `--suppressions-location {suppressions}`. So ESLint does not read the repository's own `eslint-suppressions.json`, which would accept each finding that it lists. This option needs ESLint 9.24.0 or later. The loader reads the JSON file beside it. It loads the parser and the plugins from the root of the hook's install, where each pinned package is, never from a copy that ESLint itself depends on and never from the repository's `node_modules`. If the install lost a package and Node finds one in another place, such as a folder above the install or `NODE_PATH`, the loader stops. It finds each package as `require` does, so a package that offers its entry only to `import` fails with `ERR_PACKAGE_PATH_NOT_EXPORTED`. A team definition of this format needs the same loader under the key `loader`, so copy [the built-in definition](https://github.com/karstenevers/gateseal/blob/main/src/gateseal/data/tools/eslint.tool.toml).

## Choices

A choice names a top-level configuration `key` and a `since` map. The map gives each accepted value and the earliest tool release that supports it. Values use lowercase letters and digits. Expose the choice and its default in `[capabilities.<name>.choices]` in `policy.toml`. The reserved key `extend` is invalid.
