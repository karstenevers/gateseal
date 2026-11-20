# Tool definitions

A policy can describe a tool in `tools/<name>.toml`. The definition gives its source and hooks. Its filename sets the tool name, which must match `[a-z][a-z0-9-]*`. List that name under a capability in `policy.toml`.

For example, this source fragment describes the Black hook repository:

```toml
[source]
repository = "https://github.com/psf/black-pre-commit-mirror"
tag = "{version}"
```

See [Add a tool](add-a-tool.md) for complete definitions. Built-in definitions exist for `pre-commit-hooks`, `ruff`, `pyright`, `betterleaks`, `prettier`, `eslint` and `tsc`. A team definition cannot reuse those names or the reserved names `pre-commit`, `python` and `node`.

## Shared fields

| Field | Value |
| --- | --- |
| `kind` | `pre-commit-repository`, `release-binary`, `uv-project`, `npm-package` or `node-project`. |
| `stages` | `["commit", "run", "ci"]`, `["push", "ci"]`, or `["ci"]`. |
| `scope` | `files` or `project`. With `files`, pre-commit passes matching filenames. With `project`, the hook runs at every commit, or at every push in the push stage. It gets no filenames and checks the whole project. |
| `configuration.format` | Optional `toml` or `eslint`. The generated file is `.gateseal/<name>.toml` or, for `eslint`, `.gateseal/<name>.json`. |
| `files.<key>` | Optional fixed file with lowercase `extension` and text `content` using LF line endings. |
| `hooks.<id>` | At least one hook. |
| `history.args` | Optional arguments of the tool's executable for a CI history scan, for a tool of any kind but `pre-commit-repository`. Include `{range}` exactly once. |
| `choices.<name>` | Optional choice in the tool configuration. It needs `configuration`. |

An unsupported kind or value gives `unsupported_tool_definition`. Unknown fields are invalid.

## Stages

| `stages` | The hooks run |
| --- | --- |
| `["commit", "run", "ci"]` | At commit, in `gateseal run`, and in the CI gate. |
| `["push", "ci"]` | At `git push` and in the CI gate. |
| `["ci"]` | In the CI gate alone. |

Use the push stage for a tool that is too slow for each commit. Each clone then needs the push hook. See [Checks at a push](work-in-a-configured-repository.md#checks-at-a-push). A gateseal release without the push stage gives `unsupported_tool_definition` for such a policy. Set the policy's `minimum_compiler` to the first release that has the stage.

## Hooks

Each hook has a distinct `name`, optional literal `args`, optional `types_or`, and `output`. gateseal's own hooks `gateseal-check`, `gateseal-lfs-check` and `gateseal-node-packages` keep their IDs and names. A hook with one of them gives `duplicate_identifier`. A hook of a tool with the `project` scope takes no `types_or`. Use `concise` for `file:line:column: rule message` findings. Use `plain` for pass, fail, or modified results without structured findings. Use `{configuration}` or `{<files key>}` as a whole argument to pass a generated file. Other braces are invalid. Arguments are passed without a shell.

## Sources and executables

A `pre-commit-repository` source has an HTTPS `repository` and a `tag` pattern containing `{version}`. The URL has no credentials, query, or trailing slash. The generated `policy.lock` records the upstream commit behind each reviewed tag. A Python hook uses pre-commit's Python.

A `release-binary` source also has `archive`, `checksums`, and `platforms`. The archive pattern contains `{platform}` and may contain `{version}`. The checksum filename may contain `{version}`. Map at least one of `linux-x64` and `macos-arm64` to its upstream platform name. Set `executable.name` to the bare program name.

The optional `executable.check_at_commit` defaults to `false`. When true, the policy lock records the program's SHA-256 and the commit hook checks an installed copy. The check does not run in `gateseal run` or the CI gate. mise verifies an archive's recorded SHA-256 when it downloads the archive.

A `uv-project` source has a `package`: the tool's name on PyPI, with any extras in brackets, such as `pyright[nodejs]`. Set `executable.name` to the program that the package installs. The hook runs `uv run --locked --all-packages --with <package>==<version> -- <program>` in the repository's uv project:

- The policy pins the tool, by version and without a wheel hash. `gateseal policy lock` checks that PyPI has each reviewed release. The project's uv settings choose the index. If the project installs a package of the same name and version from another source, such as a path, that package runs in the tool's place.
- The repository's `uv.lock` pins the packages that the tool reads. The hook stops when `uv.lock` is missing or out of date. The hook removes no package from `.venv`. So a package that `uv.lock` no longer holds stays there until `uv sync`, and a commit can pass that the CI gate stops.
- The tool runs on the project's Python. Its first run installs the project's packages into `.venv`, the project's environment. In a uv workspace, it installs the packages of every member. uv installs each package's dependencies and its default dependency groups, which is `dev` unless `tool.uv.default-groups` names others. It installs no extra and no other group. So put what checked code imports in a default group.
- That Python finds the tool's own dependencies before the project's packages. So make an analyzer read the project's environment by name. The bundled Pyright rules set `venvPath` and `venv`, and the [mypy example](add-a-tool.md#add-mypy-a-tool-that-imports-the-project) passes `--python-executable`. A setting that moves the environment, such as `UV_PROJECT_ENVIRONMENT`, needs the same path there.
- `check_at_commit` is invalid for this kind.
- A tool that imports the project's packages itself needs a uv cache whose path holds no space. With a space, uv starts the program outside the project. This was seen with uv 0.13.6 and 0.13.9 on Linux.

The repository needs `pyproject.toml` and `uv.lock` at its root, and `pyproject.toml` must not set `tool.uv.managed` to `false`. Otherwise `gateseal configure` and `gateseal check` stop with `missing_project`. A commit through pre-commit alone has no such check: without a project, uv starts the tool outside one. The repository must also be the root of its uv project. If a folder above it holds a uv workspace that lists the repository as a member, uv uses that workspace's `uv.lock`, and gateseal does not detect it.

A hook with the `project` scope runs at every commit and gate run, also when the change only deletes files. In the push stage, it runs at every push and gate run. A capability with such a tool cannot expose `exclude`, because the tool reads every path. The tool reads the working tree. At a commit, pre-commit hides the unstaged changes of tracked files, and untracked files stay. So a file that Git does not track can change the result of a commit. The CI gate checks a new clone.

Release binaries run through `mise --no-config exec --deny-env`. A hook of any other kind receives the caller's environment. uv then reads the caller's `UV_` settings, and a setting such as `UV_NO_PROJECT` changes what the hook does. The program reads pre-commit's variables, such as `PRE_COMMIT_FROM_REF` and `PRE_COMMIT_TO_REF` in a CI gate run on a range. Review a tool's arguments and behavior separately. For example, a scanner may read a repository's ignore file.

## Tools from npm

An `npm-package` source has a `package`: the tool's name on the npm registry, without a version. Examples are `prettier` and `@biomejs/biome`. Set `executable.name` to the program that the package installs. `check_at_commit` is invalid for this kind.

The policy pins the Node version in `node`, and can pin more packages in the tool's `packages`. See [Policy format](policy-format.md#policy-fields). Each hook runs one command, in this form:

```text
mise --no-config exec <Node request> -- /bin/sh -c '<script>' <program> <args>
```

The script is one line. It does this:

```sh
node=$(mise --no-config where <Node install> 2>/dev/null) &&
  node=$node/bin && [ -x "$node/node" ] || {
  echo "mise has no install of Node <Node version>, the pinned Node. Turn the auto-install of mise on." >&2
  exit 1
}
PATH=$node:$PATH
case ${XDG_CACHE_HOME-} in
  /*) cache=$XDG_CACHE_HOME ;;
  *) cache=$HOME/.cache ;;
esac
dir=$cache/gateseal/npm/$(uname -s)-$(uname -m)/node-<Node version>/<digest>
has() {
  case $(readlink "$1/node_modules/.bin/$0" 2>/dev/null) in
    ../<package>/*) [ -x "$1/node_modules/.bin/$0" ] ;;
    *) false ;;
  esac
}
[ -e "$dir" ] || {
  mkdir -p "${dir%/*}" && new=$(mktemp -d "$dir.XXXXXX") || exit
  "$node"/npm install --no-save --no-package-lock --ignore-scripts \
    --bin-links --install-strategy=hoisted --no-dry-run \
    --no-package-lock-only --no-global --location=project \
    --include=optional --include=peer --no-audit --no-fund \
    --no-update-notifier --no-usage --no-timing --logs-max=0 \
    --prefix "$new" --cache "$new/.npm" --logs-dir "$new/.npm/_logs" \
    <package>@<version> <name>@<version> ... >/dev/null ||
    { status=$?; rm -rf "$new"; exit "$status"; }
  rm -rf "$new/.npm"
  has "$new" || {
    rm -rf "$new"
    echo "npm installed no program $0 of <package>." >&2
    exit 127
  }
  "$node/node" -e "fs.renameSync(process.argv[1], process.argv[2])" "$new" "$dir" \
    2>/dev/null || [ -e "$dir" ] ||
    { rm -rf "$new"; echo "cannot write $dir" >&2; exit 1; }
  rm -rf "$new"
}
has "$dir" || {
  echo "No program $0 of <package> in $dir. Delete the folder to install again." >&2
  exit 127
}
bin=$dir/node_modules/.bin
PATH="$node:$bin:$PATH" exec "$bin/$0" "$@"
```

| Part | What it does |
| --- | --- |
| `mise --no-config exec <Node request> --` | Installs the Node archive that the request names by URL and SHA-256 at first use, and puts Node first on `PATH`. A wrong checksum stops the hook. No mise file of the repository or the user applies. |
| `node=$(mise --no-config where ...)` | Asks mise for the folder of the Node that it installed from the archive of the request, and puts that folder first on `PATH`. `<Node install>` is the tool name and version of the request, without its options. The script starts `node` and `npm` by their paths in that folder, so no other npm installs the packages. If mise has no such install, the hook stops with status 1, at each use. So no other `node` on `PATH` installs or runs a program, also not one of the same version. |
| `dir=...` | Names the folder of the install. It is in your cache folder: `XDG_CACHE_HOME` when that is an absolute path, and `~/.cache` otherwise. The name holds the operating system, the processor, the Node version and a digest of the install command. So each machine type, each Node and each set of pins has its own folder. Two hooks with the same Node and the same packages share one folder. |
| `[ -e "$dir" ] \|\| { ... }` | Installs only when the folder is not there. So a later use calls no npm and asks the registry nothing. |
| `npm install ... --prefix "$new"` | Installs into a new, empty folder beside the final one, which `mktemp` makes under a name of its own. So no Node project's `package.json`, `.npmrc` and `node_modules` choose or replace a package: not the repository's, and not one in a folder above it. npm still reads your own npm settings and each `npm_config_` variable of the caller, and an npm script gets those from its project's `.npmrc`. If npm fails, the hook stops with npm's message and status. |
| `--no-save --no-package-lock --ignore-scripts` | Writes only `node_modules`, and runs no install script of a package. |
| `--bin-links --install-strategy=hoisted` | Links each package's programs into `node_modules/.bin` and puts each listed package into `node_modules`, whatever your npm settings say. |
| `--no-dry-run --no-package-lock-only --no-global --location=project --include=optional --include=peer --no-usage` | Keeps your npm settings from an install that npm reports as done and that is empty, in another place, or without a package. An option on the command line wins over `.npmrc` and over each `npm_config_` variable. |
| `--no-audit --no-fund --no-update-notifier` | Skips npm's audit request, its funding note and its update check. |
| `--cache "$new/.npm" --logs-dir "$new/.npm/_logs" --no-timing --logs-max=0` | Keeps npm's downloads in the new folder, and writes no log file and no timing file. The script deletes the downloads after the install. So an install writes nothing outside `gateseal/npm` in your cache folder, whatever cache your npm settings name. |
| `<name>@<version>` | The tool's package at its selected version, then each package of `packages` at its pinned version, in the policy's order. |
| `node -e "fs.renameSync(...)"` | Gives a complete install its final name in one step. So an install that stopped halfway is never used. If two first uses run at the same time, the first one to finish keeps its install, and the other one uses it. |
| `has "$new"` and `has "$dir"` | Accept only the program that the tool's own package links into `node_modules/.bin`. If a new install does not have it, the hook fails with status 127 and the message `npm installed no program`, and the script deletes that install. So the next use installs again. If a folder with its final name does not have it, or another package links a program of the same name there, the hook fails with status 127 and the message `No program`. The script does not repair that folder, because another hook can run from it at the same time. Delete the folder, and the next use installs again. |
| `PATH=... exec "$bin/$0" "$@"` | Starts the program that `executable.name` names, with the hook's `args`, in the repository. Its `PATH` is the folder of the pinned Node, the folder of the program, and then the `PATH` of the hook. npm does not start the program, so no `node_modules/.bin` folder of the repository is on that `PATH`. So a `node` in the repository's `node_modules/.bin` does not run the program, and a program that the tool starts by name, such as `git`, is the one of your `PATH`. |

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

- **npm.** `npm ci --dry-run` checks that the lockfile matches `package.json`. Then `npm install --no-save` installs only what changed. The hook runs the npm of the pinned Node's install, with that install's folder first on `PATH`. Without that install, the hook stops with status 1, as an `npm-package` hook does.
- **pnpm.** `pnpm install --frozen-lockfile` does both. gateseal installs pnpm 10.28.0 once on the pinned Node and starts it from that install, as it starts an `npm-package` tool. If `packageManager` in `package.json` names another pnpm version, pnpm changes to it.

The hook runs these commands:

```sh
npm ci --dry-run --no-global --location=project \
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
| `plugins` | Optional table. Maps each rule prefix, such as `@typescript-eslint`, to an npm package that the tool's `packages` pin. |
| `rules` | Required table. Maps each rule to `off`, `warn` or `error`, or to an array of the severity and then the rule's options. A rule named `<prefix>/<rule>` needs its prefix in `plugins`. |

Any other key gives `invalid_field`, such as `extends`, `languageOptions` or `settings`. So no key loads another file, names a preset, or gives type information. A value that JSON cannot hold, such as a TOML date or an infinite number, gives `invalid_field` too. A definition of this format takes no choices.

The JSON file is data, not an ESLint configuration. The built-in `eslint` definition adds a loader as the fixed file `files.loader`, written as `.gateseal/eslint-loader.mjs`. Its hook passes the loader with `--config {loader}`, so ESLint runs no configuration file of the repository. The loader reads the JSON file beside it. It loads the parser and the plugins from the root of the hook's install, where each pinned package is, never from a copy that ESLint itself depends on and never from the repository's `node_modules`. If the install lost a package and Node finds one in another place, such as a folder above the install or `NODE_PATH`, the loader stops. It finds each package as `require` does, so a package that offers its entry only to `import` fails with `ERR_PACKAGE_PATH_NOT_EXPORTED`. A team definition of this format needs the same loader under the key `loader`, so copy [the built-in definition](https://github.com/karstenevers/gateseal/blob/main/src/gateseal/data/tools/eslint.tool.toml).

## Choices

A choice names a top-level configuration `key` and a `since` map. The map gives each accepted value and the earliest tool release that supports it. Values use lowercase letters and digits. Expose the choice and its default in `[capabilities.<name>.choices]` in `policy.toml`. The reserved key `extend` is invalid.
