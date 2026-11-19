# Tool definitions

A policy can describe a tool in `tools/<name>.toml`. The definition gives its source and hooks. Its filename sets the tool name, which must match `[a-z][a-z0-9-]*`. List that name under a capability in `policy.toml`.

For example, this source fragment describes the Black hook repository:

```toml
[source]
repository = "https://github.com/psf/black-pre-commit-mirror"
tag = "{version}"
```

See [Add a tool](add-a-tool.md) for complete definitions. Built-in definitions exist for `pre-commit-hooks`, `ruff`, `pyright`, and `betterleaks`. A team definition cannot reuse those names or the reserved names `pre-commit` and `python`.

## Shared fields

| Field | Value |
| --- | --- |
| `kind` | `pre-commit-repository`, `release-binary` or `uv-project`. |
| `stages` | `["commit", "run", "ci"]`, `["push", "ci"]`, or `["ci"]`. |
| `scope` | `files` or `project`. With `files`, pre-commit passes matching filenames. With `project`, the hook runs at every commit, or at every push in the push stage. It gets no filenames and checks the whole project. |
| `configuration.format` | Optional `toml`. The generated file is `.gateseal/<name>.toml`. |
| `files.<key>` | Optional fixed file with lowercase `extension` and text `content` using LF line endings. |
| `hooks.<id>` | At least one hook. |
| `history.args` | Optional arguments of the tool's executable for a CI history scan, for a `release-binary` or `uv-project` tool. Include `{range}` exactly once. |
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

Each hook has a distinct `name`, optional literal `args`, optional `types_or`, and `output`. A hook of a tool with the `project` scope takes no `types_or`. Use `concise` for `file:line:column: rule message` findings. Use `plain` for pass, fail, or modified results without structured findings. Use `{configuration}` or `{<files key>}` as a whole argument to pass a generated file. Other braces are invalid. Arguments are passed without a shell.

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

Release binaries run through `mise --no-config exec --deny-env`. A hook from a `pre-commit-repository` or `uv-project` source receives the caller's environment. uv then reads the caller's `UV_` settings, and a setting such as `UV_NO_PROJECT` changes what the hook does. The program reads pre-commit's variables, such as `PRE_COMMIT_FROM_REF` and `PRE_COMMIT_TO_REF` in a CI gate run on a range. Review a tool's arguments and behavior separately. For example, a scanner may read a repository's ignore file.

## Choices

A choice names a top-level configuration `key` and a `since` map. The map gives each accepted value and the earliest tool release that supports it. Values use lowercase letters and digits. Expose the choice and its default in `[capabilities.<name>.choices]` in `policy.toml`. The reserved key `extend` is invalid.
