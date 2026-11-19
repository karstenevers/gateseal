# Add a tool to your policy

A team policy can add a release binary or a Python pre-commit repository. Start with a [bundled policy copy](author-a-policy.md#start-a-policy) in `policy/`. The examples need GitHub access. See [Tool definitions](tool-definitions.md) for the fields.

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
