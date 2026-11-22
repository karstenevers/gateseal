# Work in a configured repository

Install Git 2.54.0 or later and pre-commit 4.7.0 or later. If the policy selects a release binary such as Betterleaks, install mise 2026.10.19 or later too. If it selects a tool that runs in the uv project, such as Pyright, install uv 0.13.6 or later too. A separate gateseal installation is not needed to commit.

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

Replace `example.txt` with your changed file. The first hook runs the version recorded in `gateseal.lock`, the Lock. The Lock records the policy selection and generated files. The hook refuses staged generated files that differ from the Lock. The other hooks check staged files. A hook that checks the whole project, such as Pyright, runs at every commit. Stage `.pre-commit-config.yaml` in full. pre-commit stops if it has unstaged changes.

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

A push then checks the files that it changes. A failed check stops the push before the remote changes. The CI gate runs the same checks, so a clone without the push hook is still checked there. A check can read the pushed range from `PRE_COMMIT_FROM_REF` and `PRE_COMMIT_TO_REF`. Both are empty when pre-commit checks all files, such as at the first push of a repository.

pre-commit sets two limits at a push, which the CI gate does not have:

- It checks each file as the clone holds it, not as the pushed commit holds it. Check out the branch that you push, and commit your changes first.
- When one push updates more than one branch, it checks only one of them.

If the repository later drops its checks at a push, remove the push hook, because it keeps Git LFS from installing its own. `gateseal setup --refresh` removes the push hook that it wrote, once no worktree of the clone has checks at a push. `status` names such a hook. After `pre-commit install`, run `pre-commit uninstall --hook-type pre-push`.

### Git LFS

Git LFS uploads files from its own push hook, and the push hook of pre-commit takes its place. Uploads continue in two cases:

- Git LFS was set up before `pre-commit install`. pre-commit keeps the hook of Git LFS as `pre-push.legacy` and runs it first.
- You ran `gateseal setup --refresh`. With the push hook, it adds a hook to the clone's Git configuration that starts Git LFS at each push, on every branch and in every worktree. The hook does nothing where Git LFS is not set up, or where the clone holds no Git LFS object. Where the clone holds Git LFS objects but `git-lfs` is missing, the hook stops the push. In a clone with the push hook, `git lfs install` reports `Hook already exists: pre-push`. Uploads still work, but Git LFS skips its other hooks, which only files that Git LFS locks need. To add them, run `git lfs update --manual` and add each hook that it prints, except `pre-push`.

In both cases, Git LFS uploads before the checks at a push run. A push that a check stops can leave its LFS objects on the remote, but the remote branch does not change.

After a plain `pre-commit install`, Git LFS set up later cannot write its hook. A push then stops with this message:

```text
gateseal: This repository tracks files with Git LFS, but no push hook of this clone starts Git LFS, so a push uploads no LFS object.
```

To repair the clone, run `gateseal setup --refresh`. Without gateseal, run these commands from the clone's root. Then push again:

```sh
git lfs update --force
pre-commit install
```

Those commands replace a push hook that pre-commit kept as `pre-push.legacy`. In a clone with that file, the message names another repair: add the line `git lfs pre-push "$@"` to the file, and make the file executable.

The check also stops a push when the clone tracks no file with Git LFS now, but a commit that the remote lacks holds a Git LFS pointer. It accepts any push hook of Git's configuration that runs `git lfs pre-push`.

The check runs only on a branch whose configuration has checks at a push. Until you repair the clone, a push from another branch or worktree uploads no LFS object, and nothing stops it.

## Add your own hooks

Personal hooks run during an ordinary Git commit. They do not run in `gateseal run`, the CI gate, `status`, or `doctor`. For a shared check, ask the repository maintainer to add a [repository hook](adjust-a-setup.md#add-a-repository-hook).

For example, configure an executable script at `$HOME/bin/check-notes` for this clone:

```sh
git config hook.notes.event pre-commit
git config hook.notes.command "$HOME/bin/check-notes"
```

Alternatively, pre-commit runs an executable `pre-commit.legacy` file in the clone's hooks directory first. Do not edit the generated `.pre-commit-config.yaml` to add a personal hook.
