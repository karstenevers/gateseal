# Work in a configured repository

Install Git 2.54.0 or later and pre-commit 4.7.0 or later. If the policy selects a release binary such as Betterleaks, install mise 2026.10.19 or later too. A separate gateseal installation is not needed to commit.

From each clone's root, install the commit hook:

```sh
pre-commit install
```

The first commit needs network access to download the hook environments and selected binaries. Later commits reuse them. Linked worktrees share the clone's hook.

## Commit changes

Stage your changes. Then commit them:

```sh
git add example.txt
git commit -m 'Update example'
```

Replace `example.txt` with your changed file. The first hook runs the version recorded in `gateseal.lock`, the Lock. The Lock records the policy selection and generated files. The hook refuses staged generated files that differ from the Lock. The other hooks check staged files. Stage `.pre-commit-config.yaml` in full. pre-commit stops if it has unstaged changes.

To check all tracked files, run:

```sh
pre-commit run --all-files
```

If you installed gateseal and ran [setup](install.md#set-up-pre-commit-on-this-machine), `gateseal run` does the same.

If a hook changes a file, review the change. Stage it. Retry the commit. If Betterleaks reports a real credential, remove it. Rotate it. See [Troubleshoot gateseal](troubleshooting.md) for other errors.

## Add your own hooks

Personal hooks run during an ordinary Git commit. They do not run in `gateseal run`, the CI gate, `status`, or `doctor`. For a shared check, ask the repository maintainer to add a [repository hook](adjust-a-setup.md#add-a-repository-hook).

For example, configure an executable script at `$HOME/bin/check-notes` for this clone:

```sh
git config hook.notes.event pre-commit
git config hook.notes.command "$HOME/bin/check-notes"
```

Alternatively, pre-commit runs an executable `pre-commit.legacy` file in the clone's hooks directory first. Do not edit the generated `.pre-commit-config.yaml` to add a personal hook.
