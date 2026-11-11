# Add a tool to your policy

You can add a [team-defined tool](../GLOSSARY.md#team-defined-tool) when the
compiler supports its kind. The examples use both supported kinds.

| To add | Tool kind | Example |
| --- | --- | --- |
| A binary from a release with checksums | `release-binary` | [Add Gitleaks to secret detection](#define-the-tool) |
| A Python-language pre-commit hook repository | `pre-commit-repository` | [Offer Black as an alternative to Ruff](#offer-black-as-a-formatter-on-a-newer-python) |

Start with [a copy of the bundled policy](author-a-policy.md#start-the-policy)
in `policy/`. From the root of the policy repository, create the
`policy/tools/` directory if needed. Use the gateseal release your
repositories use and a machine with access to GitHub. See the
[tool definition reference](tool-definitions.md) for other fields.

## Define the tool

Keep Betterleaks enabled in this example. It checks the staged file list.
When Gitleaks 8.30 receives multiple paths, it scans the whole worktree
and reads its ignore file. See
[What the Gitleaks definition guarantees](#what-the-gitleaks-definition-guarantees).

Save the definition as `policy/tools/gitleaks.toml`. The filename gives the
tool its name. Choose a name that the compiler has not reserved.

<!-- policy file: tools/gitleaks.toml -->
```toml
# Gitleaks 8.30 takes one path. When pre-commit passes it two or more files,
# it scans the whole worktree, untracked files included, and reads
# .gitleaksignore at its top, whatever --gitleaks-ignore-path says.
kind = "release-binary"
stages = ["commit", "run", "ci"]
scope = "files"
removed_prefixes = ["GITLEAKS_"]

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
version = ["version"]
version_output = "{version}"

# The report prints each finding's place and rule, never its source line.
[files.report]
extension = "tmpl"
content = '''
{{ range . }}{{ .File }}:{{ .StartLine }}:{{ .StartColumn }}: {{ .RuleID }} ({{ .Description }})
{{ end }}{{ if . }}Next: Remove each secret, stage the fix, and retry. Rotate any secret that was real.
{{ end -}}
'''

# --ignore-gitleaks-allow turns off inline gitleaks:allow comments. The
# final -- ends the options before pre-commit appends file names.
[hooks.gitleaks]
name = "Detect secrets with Gitleaks"
args = [
  "dir", "--config", "{configuration}",
  "--gitleaks-ignore-path", "/dev/null", "--ignore-gitleaks-allow",
  "--redact", "--no-banner", "--no-color",
  "--report-format", "template", "--report-template", "{report}",
  "--report-path", "-", "--",
]
probe = ["gitleaks", "stdin", "--config", "{configuration}", "--no-banner"]
output = "concise"
```

`source` describes the release archives and their checksum file.
`executable` tells activation to check the output of `gitleaks version`.
In the hook arguments, `{configuration}` names the generated
`.gateseal/gitleaks.toml`, and `{report}` names the generated
`.gateseal/gitleaks-report.tmpl`. The report prints each finding as
`file:line:column: rule (description)` for gateseal to read.

Save the rules as `policy/rules/gitleaks.toml`. Use UTF-8 TOML with LF line
endings. A rule file cannot extend another configuration. The allowlist
excludes Git's own directory from whole-worktree scans.

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

## Add it to a capability

In `policy/policy.toml`, replace the existing `[capabilities.secrets]`
table with the one below. Add the `tools.gitleaks` and `components.gitleaks`
tables. Each tool belongs to one capability.

```toml
[capabilities.secrets]
tools = ["betterleaks", "gitleaks"]

[tools.gitleaks]
configuration = "rules/gitleaks.toml"

[components.gitleaks]
pin = "8.30.0"

[components.gitleaks.versions."8.30.0"]
```

Generate the lock and check the policy.

```sh
gateseal policy lock policy
gateseal policy check policy
```

The lock command reads `gitleaks_8.30.0_checksums.txt` from the release and
downloads each platform's archive. Review the URL and verified checksum
for each platform in `git diff -- policy/policy.lock`.

## Review a new release

After reviewing Gitleaks 8.30.1, add the version and pin it.

```sh
gateseal policy add gitleaks 8.30.1 policy
gateseal policy pin gitleaks 8.30.1 policy
gateseal policy check policy
```

[Test the candidate](author-a-policy.md#try-a-candidate-policy-before-you-tag-it)
and [tag the release](author-a-policy.md#release-the-policy). In a
repository that adopts it and selects `secrets`, run `gateseal status`.
Look for Betterleaks marked `built-in` and Gitleaks marked `team-defined`.
Both hooks redact secret values from their findings.

## Offer Black as a formatter on a newer Python

Use this example when some repositories need Black and others use Ruff for
formatting. Black parses code with the interpreter it runs on. To format
syntax newer than the compiler runtime supports, the policy must also select
a [Hook Python](../GLOSSARY.md#hook-python) that can parse it.

Save the definition as `policy/tools/black.toml`.

<!-- policy file: tools/black.toml -->
```toml
# Black parses the code it formats with the interpreter it runs on, so it
# formats syntax newer than gateseal's runtime CPython only on a Hook Python
# that knows that syntax. The mirror's hook installs Black from PyPI by
# version and without a hash, so the prepared black must report its pin.
kind = "pre-commit-repository"
stages = ["commit", "run", "ci"]
scope = "files"
removed_prefixes = ["BLACK_"]

[source]
repository = "https://github.com/psf/black-pre-commit-mirror"
tag = "{version}"

[configuration]
format = "toml"

[executable]
name = "black"
version = ["--version"]
version_output = 'black, {version} \(compiled: [a-z]+\)\nPython \(CPython\) [0-9.]+'

# With --config, Black reads no other configuration. The final -- ends the
# options before pre-commit appends file names.
[hooks.black]
name = "black"
args = ["--config", "{configuration}", "--"]
types_or = ["python", "pyi"]
probe = ["black", "--config", "{configuration}", "--check", "-"]
output = "plain"
```

Save the configuration as `policy/rules/black.toml`. Black reads its
settings from the `[tool.black]` table.

<!-- policy file: rules/black.toml -->
```toml
[tool.black]
line-length = 88
```

In `policy/policy.toml`, replace `[capabilities.python]` with the table
below. Add the other tables in the example. The default keeps Ruff as both
linter and formatter. Selecting Black changes the formatter and keeps the
Ruff lint hook.

<!-- policy tables: black -->
```toml
[capabilities.python]
tools = ["ruff", "black"]
alternatives.formatter = ["ruff-format", "black"]

[tools.black]
configuration = "rules/black.toml"

[choices.python.formatter]
default = "ruff-format"

[components.black]
pin = "26.10.1"

[components.black.versions."26.10.1"]
```

Lock the Black release. Then add the Hook Python. For a new CPython version,
use the patch release available in the latest python-build-standalone build.
This example uses 3.15.2.

```sh
gateseal policy lock policy
gateseal policy add python 3.15.2 policy
gateseal policy check policy
```

The first `policy add python` pins the interpreter in `[components.python]`.
It records each supported machine's download data in `policy.lock` using
the build's `SHA256SUMS`. If the policy already pins a different Hook
Python, also run `gateseal policy pin python 3.15.2 policy`.

The pin applies to all Python-language hooks from pre-commit repositories,
including Ruff and pre-commit-hooks. Without a pin, they use the compiler
runtime's CPython. Release binaries run independently of this choice.

After a repository adopts the release, select Black in its `gateseal.toml`.
For code that uses Python 3.15 syntax, also tell Ruff which syntax to accept.

```toml
[choices.python]
formatter = "black"
target_version = "py315"
```

Run `gateseal configure` and `gateseal activate` in that repository.
Run `gateseal status` and look for the selected Hook Python and
`Choice python.formatter: black.` If Black rejects newer syntax, check the
interpreter in the status output.

## What the Gitleaks definition guarantees

This definition uses `--redact` and a report template that omits source
text. It disables inline `gitleaks:allow` comments. The
[tool kind](tool-definitions.md#what-a-team-definition-guarantees) verifies
the executable, but finding suppression and file selection depend on how
Gitleaks handles its arguments.

Gitleaks 8.30 accepts one path. When pre-commit supplies multiple filenames,
Gitleaks scans the worktree, including untracked and ignored files, and reads
the worktree's `.gitleaksignore`. This has three effects.

- A secret in an ignored `.env` can block a commit of unrelated files.
- A repository's `.gitleaksignore` can hide a finding from Gitleaks.
- pre-commit can send a long file list in batches. Gitleaks can report the
  same finding once per batch.

Betterleaks takes the file list and ignores repository ignore files. Keeping
it in the same capability preserves the check on staged files even when
the repository suppresses a Gitleaks finding.
