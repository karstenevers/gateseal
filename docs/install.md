# Install gateseal

Repository maintainers use gateseal to configure and upgrade checks. Teammates do not need gateseal to commit, as [Work in a configured repository](work-in-a-configured-repository.md) shows.

Install [uv](https://docs.astral.sh/uv/). Then run:

```sh
uv tool install gateseal
```

In a configured repository, install the version that `gateseal.lock` names under `gateseal.version`. Replace `VERSION` with it:

```sh
uv tool install "gateseal==VERSION"
```

## Requirements

| Requirement | Version and use |
| --- | --- |
| Git | 2.54.0 or later for repository commands and commits. |
| uv | 0.13.6 or later is tested for installation and setup, and for commits when the policy selects a tool that runs in the uv project, such as Pyright. |
| pre-commit | 4.7.0 or later for commits when installed separately. |
| mise | 2026.10.19 or later for release binaries such as Betterleaks, and for tools from npm such as ESLint. These need no Node or npm on the machine. |

gateseal supports macOS on arm64 and Linux on x86-64 with glibc. It does not support Windows. WSL, Intel Mac, Linux on arm64, and Linux with musl are not tested. Git submodules are not covered.

## Set up pre-commit on this machine

`gateseal setup` is optional. It installs pre-commit once on the machine and adds the commit hook to each new clone:

```sh
gateseal setup
```

For a clone that you made before setup, run this in the clone:

```sh
gateseal setup --refresh
```

A repository with [checks at a push](work-in-a-configured-repository.md#checks-at-a-push) needs that command in each clone, new or old. A new clone gets only the commit hook, so that Git LFS can install its push hook.

After setup, `gateseal run` checks all files, and `gateseal status` reports whether setup is ready.

To use your own pre-commit instead, run `pre-commit install` in each clone. If `core.hooksPath` is set, see [Troubleshoot gateseal](troubleshooting.md#git-uses-a-different-hooks-directory).

> [!NOTE]
> Setup puts pre-commit in `~/.local/share/gateseal`, or in `$XDG_DATA_HOME/gateseal` when that is set. It sets Git's global `init.templateDir` so Git copies the hook into new clones. If you already have a template folder or `core.hooksPath`, setup reports a conflict and changes nothing. It warns if mise is missing or too old. An existing hook stays as `pre-commit.legacy` and runs first.
>
> To undo setup, run `uvx pre-commit uninstall` in each clone. Where `gateseal setup --refresh` added the hook `gateseal-lfs-upload`, also run `git config --local --remove-section hook.gateseal-lfs-upload`. If the clone uses Git LFS, then run `git lfs update`, so that Git LFS writes its push hook again. Then remove the global `init.templateDir` setting and the installation folder, but only if setup created them.
