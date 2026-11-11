# Configure a repository

Start here to add gateseal to a Git repository. If the repository already
contains `gateseal.lock`, follow [Work in a configured
repository](work-in-a-configured-repository.md).

## Configure and commit

[Install gateseal](install.md#install-from-pypi), then [pin the release in the
repository](install.md#pin-the-release-in-a-repository). The example
`configure` command selects the bundled `hygiene` capability. It checks for
trailing whitespace, missing final newlines, and invalid YAML. To select more
checks at the same time, see [Add capabilities](#add-capabilities).

Review the three files that `configure` creates for `hygiene`:

| File | What to review |
| --- | --- |
| `gateseal.toml` | The Manifest selects the policy and the checks you want. |
| `gateseal.lock` | The Lock records the exact compiler and policy, tool versions, and generated files. |
| `.pre-commit-config.yaml` | The pre-commit hooks that gateseal generated from your selection. |

From the repository root, check that these files agree and prepare the tools
for this worktree:

```sh
gateseal check
gateseal activate
```

Stage the files. If you enabled Python, secrets, or CI, also stage the files
listed below for those selections. Commit, then check all tracked files:

```sh
git add gateseal.toml gateseal.lock .pre-commit-config.yaml
git commit -m 'Adopt gateseal checks'
gateseal run
```

If a hook changes a file, review the change, stage it, and try the commit
again. [When a commit stops](work-in-a-configured-repository.md#when-a-commit-stops)
covers other findings.

## Add capabilities

On the first `configure`, you can add `--capability python` or
`--capability secrets` alongside `--capability hygiene`.

To add checks later, edit `capabilities` in `gateseal.toml`. For example,
select `["hygiene", "python"]` to add Python checks. Run:

```sh
gateseal configure
gateseal activate
git add gateseal.toml gateseal.lock .pre-commit-config.yaml .gateseal
```

Review the staged changes and commit. Keep each generated file fully staged.

### Check Python with Ruff

With the bundled `python` capability, Ruff runs `ruff check` and then
`ruff format` on changed Python files, stubs, and notebooks. It uses the
release selected by the policy or a reviewed version exception. Its rules
come from `.gateseal/ruff.toml`. Stage this file with the Lock.

Ruff applies safe fixes. For other findings, it reports the file, line,
column, and rule. Fix the reported line, review any automatic changes, then
stage and commit again. To see findings across all tracked files, run
`gateseal run --json`. To check the prepared Ruff version, run
`gateseal doctor`.

### Detect secrets

The bundled `secrets` capability runs Betterleaks on staged changes. A
finding identifies the file, line, column, and rule. The output hides the
secret. Remove the secret, rotate a real credential, then stage and commit
the fix. A false positive needs a change to the reviewed policy rules.

Activation uses mise to install Betterleaks. Stage the generated Betterleaks
rules, both report templates, and the mise configuration under `.gateseal`.
After activation, commits and `gateseal run` use the prepared binary directly.

## Check in GitHub CI

For a new repository, select a shared wheel and add `--ci github` to the
first `configure` command. For an existing repository, follow [Turn it
on](github-ci.md#turn-it-on). Commit the generated
`.github/workflows/gateseal.yml` along with the Lock.

## What a commit checks

A commit checks the files you staged. The staged `gateseal.lock` selects the
gateseal version and the checks, even if you change the Lock again after you
stage it. [Which files run](how-a-commit-runs.md#which-files-run) covers
`git commit -a` and the other ways Git chooses the files.
