# Add a tool to your policy

A team policy can add a release binary, a Python pre-commit repository, or a tool from npm. It can also add a tool that reads the repository's uv project or Node project. Start with a [bundled policy copy](author-a-policy.md#start-a-policy) in `policy/`. The examples need GitHub access. See [Tool definitions](tool-definitions.md) for the fields.

## Add Gitleaks

Keep Betterleaks enabled in this example. Save this definition as `policy/tools/gitleaks.toml`:

<!-- policy file: tools/gitleaks.toml -->
```toml
kind = "release-binary"
stages = ["commit", "run", "ci"]
scope = "files"

[source]
repository = "https://github.com/gitleaks/gitleaks"
tag = "v{version}"
archive = "gitleaks_{version}_{platform}.tar.gz"
checksums = "gitleaks_{version}_checksums.txt"

[source.platforms]
linux-x64 = "linux_x64"
macos-arm64 = "darwin_arm64"

[configuration]
format = "toml"

[executable]
name = "gitleaks"

[files.report]
extension = "tmpl"
content = '''
{{ range . }}{{ .File }}:{{ .StartLine }}:{{ .StartColumn }}: {{ .RuleID }} ({{ .Description }})
{{ end }}{{ if . }}Next: Remove each secret. Stage the fix. Retry. Rotate any secret that was real.
{{ end -}}
'''

[hooks.gitleaks]
name = "Detect secrets with Gitleaks"
args = [
  "dir", "--config", "{configuration}",
  "--gitleaks-ignore-path", "/dev/null", "--ignore-gitleaks-allow",
  "--redact", "--no-banner", "--no-color",
  "--report-format", "template", "--report-template", "{report}",
  "--report-path", "-", "--",
]
output = "concise"
```

Save these rules as `policy/rules/gitleaks.toml`:

<!-- policy file: rules/gitleaks.toml -->
```toml
title = "Team secret rules for Gitleaks"

[[rules]]
id = "aws-access-token"
description = "AWS access key identifier"
regex = '''\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'''
keywords = ["akia", "asia"]

[[rules]]
id = "github-pat"
description = "GitHub personal access token"
regex = '''\bgh[pousr]_[A-Za-z0-9._-]{36,}'''
keywords = ["ghp_", "gho_", "ghu_", "ghs_", "ghr_"]

[[allowlists]]
description = "Git's own directory"
paths = ['''^\.git/''']
```

Add this table after the Betterleaks table in `policy/policy.toml`:

<!-- policy tables: gitleaks -->
```toml
[capabilities.secrets.tools.gitleaks]
configuration = "rules/gitleaks.toml"
pin = "8.30.0"
reviewed = ["8.30.0"]
```

Generate the policy lock, `policy/policy.lock`. Check it before committing. It records the reviewed upstream commits and archive checksums:

```sh
gateseal policy lock policy
gateseal policy check policy
```

Review the URLs and checksums in the policy lock. Test the policy before [releasing it](author-a-policy.md#release-the-policy).

The definition redacts findings and disables inline allow comments. Gitleaks 8.30 can scan the whole worktree when pre-commit passes multiple paths. It can read `.gitleaksignore` and report a finding more than once. Betterleaks remains enabled to check staged files without those ignore rules.

## Offer Black as a formatter

Save this definition as `policy/tools/black.toml`:

<!-- policy file: tools/black.toml -->
```toml
kind = "pre-commit-repository"
stages = ["commit", "run", "ci"]
scope = "files"

[source]
repository = "https://github.com/psf/black-pre-commit-mirror"
tag = "{version}"

[configuration]
format = "toml"

[hooks.black]
name = "black"
args = ["--config", "{configuration}", "--"]
types_or = ["python", "pyi"]
output = "plain"
```

Save this configuration as `policy/rules/black.toml`:

<!-- policy file: rules/black.toml -->
```toml
[tool.black]
line-length = 88
```

Replace the Python capability tables in `policy/policy.toml` with these tables:

<!-- policy tables: black -->
```toml
[capabilities.python.tools.ruff]
hook_ids = ["ruff-check", "ruff-format"]
configuration = "rules/ruff.toml"
pin = "0.17.5"
reviewed = ["0.17.5", "0.17.1"]

[capabilities.python.tools.black]
configuration = "rules/black.toml"
pin = "26.10.1"
reviewed = ["26.10.1"]

[capabilities.python.alternatives]
formatter = ["ruff-format", "black"]

[capabilities.python.choices]
target_version = "py311"
formatter = "ruff-format"
```

Run `gateseal policy lock policy` and `gateseal policy check policy`. After a repository adopts the release, it can select Black with this `gateseal.toml` fragment:

```toml
[choices.python]
formatter = "black"
```

Run `gateseal configure` in that repository. Ruff remains the linter. The Black hook uses pre-commit's Python and installs the pinned Black version from PyPI without a wheel hash.

## Add mypy, a tool that imports the project

A type checker reads the packages that the project installs. A `uv-project` tool runs in the repository's own uv project, so it finds them. The bundled `python-types` capability runs Pyright this way. This example runs mypy in its place.

Save this definition as `policy/tools/mypy.toml`:

<!-- policy file: tools/mypy.toml -->
```toml
kind = "uv-project"
stages = ["commit", "run", "ci"]
scope = "project"

[source]
package = "mypy"

[executable]
name = "mypy"

[hooks.mypy]
name = "mypy"
args = ["--strict", "--python-executable", ".gateseal/environments/mypy/bin/python", "."]
output = "plain"
```

Replace the `python-types` table in `policy/policy.toml` with this table:

<!-- policy tables: mypy -->
```toml
[capabilities.python-types.tools.mypy]
pin = "2.4.1"
reviewed = ["2.4.1"]
```

Run `gateseal policy lock policy` and `gateseal policy check policy`. The lock command checks that PyPI has the release.

A repository that adopts the release needs a `pyproject.toml` and a `uv.lock` at its root. Its generated hook is one command:

```sh
env UV_PROJECT_ENVIRONMENT=.gateseal/environments/mypy uv run --locked --exact --all-packages --with mypy==2.4.1 -- mypy --strict --python-executable .gateseal/environments/mypy/bin/python .
```

- mypy gets its own environment, `.gateseal/environments/mypy`. uv installs exactly what the repository's `uv.lock` pins into it, removes every other package, and adds the pinned mypy over it. Your `.venv` stays as it is.
- `--python-executable` makes mypy read the packages of that environment. Without it, mypy reads the packages of the Python that starts it. That Python finds mypy's own dependencies before the project's, so a package that both install would have the wrong version.
- The hook stops when `uv.lock` is missing or out of date.
- If the checked code imports from an extra or a dependency group that uv does not install by default, list it in `[source]`, such as `extras = ["cli"]` or `groups = ["typing"]`.
- With `scope = "project"`, the hook runs at every commit. mypy gets no file names and checks the whole project. So it also finds an error in a file that the commit does not change, and an error that a deleted file causes.

## Format Markdown with Prettier from npm

An `npm-package` tool installs a package from the npm registry and runs it on the Node that the policy pins. The bundled `typescript` capability runs Prettier this way on TypeScript files. This example adds a second Prettier for Markdown files, with options of its own. The built-in name `prettier` is taken, so the team tool needs another name.

Save this definition as `policy/tools/prettier-markdown.toml`:

<!-- policy file: tools/prettier-markdown.toml -->
```toml
kind = "npm-package"
stages = ["commit", "run", "ci"]
scope = "files"

[source]
package = "prettier"

[executable]
name = "prettier"

[configuration]
format = "toml"

[files.ignore]
extension = "txt"
content = """
# This file lists no file, so Prettier skips none.
"""

[hooks.prettier-markdown]
name = "Format Markdown with Prettier"
args = [
  "--config", "{configuration}",
  "--no-editorconfig", "--ignore-path", "{ignore}",
  "--write", "--no-error-on-unmatched-pattern", "--",
]
types_or = ["markdown"]
output = "plain"
```

Save these options as `policy/rules/prettier-markdown.toml`:

<!-- policy file: rules/prettier-markdown.toml -->
```toml
printWidth = 80
proseWrap = "always"
```

Add these tables at the end of `policy/policy.toml`:

<!-- policy tables: prettier-markdown -->
```toml
[capabilities.docs.tools.prettier-markdown]
configuration = "rules/prettier-markdown.toml"
pin = "3.10.1"
reviewed = ["3.10.1"]

[capabilities.docs.choices]
exclude = []
```

Every tool from npm runs on the Node that `node` pins at the top of `policy/policy.toml`. The bundled policy copy has this line already. Add it to a policy without it:

<!-- policy tables: node -->
```toml
node = "24.22.3"
```

Run `gateseal policy lock policy` and `gateseal policy check policy`. The lock command records the SHA-256 of each Node archive from nodejs.org. It also runs npm on that Node to lock Prettier 3.10.1: npm's lockfile records Prettier and each package that it needs, with its integrity hash.

A repository that adopts the release selects the tool with `--capability docs`. Each commit then formats the staged Markdown files:

- mise installs the pinned Node at the first commit and checks its archive. npm installs Prettier into its own cache, outside the repository.
- `--no-editorconfig` and `--ignore-path` keep the repository's `.editorconfig`, `.prettierignore` and `.gitignore` from changing the result. The ignore path is the fixed file `files.ignore`, which gateseal writes as `.gateseal/prettier-markdown-ignore.txt`. It lists no file, and it exists on every platform.
- `--no-error-on-unmatched-pattern` lets a commit pass when Prettier skips every staged file, such as one in `node_modules`.
- `--` ends the options, so Prettier formats a file named like an option as a file.

The hook skips every file that gateseal generates. See [the `npm-package` kind](tool-definitions.md#tools-from-npm) for what the kind guarantees.

## Check a second tsconfig with strict tsc flags

A `node-project` tool runs from npm on the pinned Node, like an `npm-package` tool. It also reads the packages of the repository's Node project. gateseal installs them from the project's lockfile before the tool runs. The bundled `typescript-types` capability runs `tsc` this way on the project's `tsconfig.json`. This example adds a second check for a project whose application has its own `tsconfig.app.json`. The built-in name `tsc` is taken, so the team tool needs another name.

Save this definition as `policy/tools/tsc-app.toml`:

<!-- policy file: tools/tsc-app.toml -->
```toml
kind = "node-project"
stages = ["push", "ci"]
scope = "project"

[source]
package = "typescript"

[executable]
name = "tsc"

[hooks.tsc-app]
name = "Check the types of the app with tsc"
args = ["--noEmit", "--project", "tsconfig.app.json", "--strict"]
output = "plain"
```

Add this table at the end of `policy/policy.toml`:

<!-- policy tables: tsc-app -->
```toml
[capabilities.app-types.tools.tsc-app]
pin = "7.0.2"
reviewed = ["7.0.2"]
```

The tool runs on the Node that `node` pins, as [the Prettier example](#format-markdown-with-prettier-from-npm) shows. Run `gateseal policy lock policy` and `gateseal policy check policy`. The lock command runs npm on that Node to lock TypeScript 7.0.2: npm's lockfile records TypeScript and each package that it needs, with its integrity hash.

A repository that adopts the release selects the tool with `--capability app-types`. It needs `package.json` and one lockfile at its root, `package-lock.json` or `pnpm-lock.yaml`. Each push then runs two hooks:

1. `gateseal-node-packages` installs the packages that the lockfile pins into `node_modules`.
2. `tsc-app` runs `tsc --noEmit --project tsconfig.app.json --strict` with the pinned TypeScript.

The definition sets how the check runs:

- With `stages = ["push", "ci"]`, the check runs at `git push` and in the CI gate, not at a commit.
- With `scope = "project"`, `tsc` gets no file names. `tsconfig.app.json` chooses the files.
- A flag in `args` wins over the same option in the project's `tsconfig.json`. So `--strict` holds when `tsconfig.app.json` sets `"strict": false`, and the policy owns the strict options.
- The project's own `typescript` package does not run in place of the pinned one.

See [the `node-project` kind](tool-definitions.md#tools-that-read-the-node-project) for what the kind guarantees.
