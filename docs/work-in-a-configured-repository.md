# Work in a configured repository

You do not need gateseal to commit or push. Install these tools at the versions that [Requirements](install.md#requirements) gives:

- Git and pre-commit.
- mise, if the checks include a release binary such as Betterleaks, or a tool from npm such as ESLint. You need no Node, npm, or pnpm.
- uv, if the checks include a tool that runs in the uv project, such as Pyright.

From each clone's root, install the commit hook:

```sh
pre-commit install
```

The first commit needs network access to download the hook environments, selected binaries, and the Node and npm packages of a tool from npm. Later commits reuse them. Linked worktrees share the clone's hook.

## Commit changes

Stage your changes and commit them as usual. The first hook refuses staged generated files that differ from `gateseal.lock`, the Lock. The other hooks check the staged files. A hook that checks the whole project, such as Pyright, runs at every commit. Stage `.pre-commit-config.yaml` in full, because pre-commit stops if it has unstaged changes.

To check all tracked files, run:

```sh
pre-commit run --all-files
```

If you installed gateseal and ran [setup](install.md#set-up-pre-commit-on-this-machine), `gateseal run` does the same.

If a hook changes a file, review the change. Stage it. Retry the commit. If Betterleaks reports a real credential, remove it. Rotate it. See [Troubleshoot gateseal](troubleshooting.md) for other errors.

## Checks at a push

A repository can run its slow checks at `git push` instead of at commit. Its `.pre-commit-config.yaml` then lists `pre-push` under `default_install_hook_types`. From the clone's root, install the commit hook and the push hook:

```sh
pre-commit install
```

Run the command again in a clone that has only the commit hook. If you ran setup, `gateseal setup --refresh` does the same.

To run the checks of a push without a push, run:

```sh
pre-commit run --hook-stage pre-push --all-files
```

A push then checks the files that it changes. A check of the whole project, such as `tsc`, runs at every push. A failed check stops the push before the remote changes. The CI gate runs the same checks, so a clone without the push hook is still checked there.

pre-commit sets two limits at a push, which the CI gate does not have:

- It checks each file as the clone holds it, not as the pushed commit holds it. Check out the branch that you push, and commit your changes first.
- When one push updates more than one branch, it checks only one of them.

If the repository later drops its checks at a push, remove the push hook, because it keeps Git LFS from installing its own. Run `gateseal setup --refresh`, or `pre-commit uninstall --hook-type pre-push` after a plain `pre-commit install`.

### Git LFS

Git LFS uploads files from its own push hook, and the push hook of pre-commit takes its place. To keep the uploads, set up Git LFS before `pre-commit install`, or run `gateseal setup --refresh`. Git LFS then uploads before the checks at a push run.

If a push says that no push hook starts Git LFS, see [Troubleshoot gateseal](troubleshooting.md#a-push-runs-no-checks-or-stops-for-git-lfs).

## Add your own hooks

Personal hooks run during an ordinary Git commit. They do not run in `gateseal run`, the CI gate, or `status`. For a shared check, ask the repository maintainer to add a [repository hook](adjust-a-setup.md#add-a-repository-hook).

For example, configure an executable script at `$HOME/bin/check-notes` for this clone:

```sh
git config hook.notes.event pre-commit
git config hook.notes.command "$HOME/bin/check-notes"
```

Alternatively, pre-commit runs an executable `pre-commit.legacy` file in the clone's hooks directory first. Do not edit the generated `.pre-commit-config.yaml` to add a personal hook.
