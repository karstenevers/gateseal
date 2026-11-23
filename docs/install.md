# Install gateseal

Repository maintainers use gateseal to configure and upgrade checks. Teammates can [install the commit hook](work-in-a-configured-repository.md) without installing gateseal.

Install [uv](https://docs.astral.sh/uv/). Then run:

```sh
uv tool install gateseal
```

In a configured repository, install the version under `gateseal.version` in `gateseal.lock`. Replace `VERSION` with that value:

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

The supported platforms are macOS on arm64 and Linux on x86-64 with glibc. Windows is unsupported. WSL, Intel Mac, Linux arm64, and Linux with musl are untested. Git submodules are not covered.

## Set up pre-commit on this machine

`gateseal setup` installs pre-commit once per machine and adds the commit hook to every new clone. It is optional. Teammates can also commit with their own pre-commit and `pre-commit install`. Run:

```sh
gateseal setup
```

New clones get the commit hook. For a clone you made before setup, run this in the clone:

```sh
gateseal setup --refresh
```

A repository with [checks at a push](work-in-a-configured-repository.md#checks-at-a-push) needs that command in each clone, new or old. New clones get the commit hook alone, so that Git LFS can install its push hook.

After setup, `gateseal run` checks all files with that pre-commit. `gateseal status` also reports whether it is ready.

To use your own pre-commit instead, run `pre-commit install` in each clone. If `core.hooksPath` is set, see [Troubleshoot gateseal](troubleshooting.md#git-uses-a-different-hooks-directory).

> [!NOTE]
> Setup puts pre-commit in `~/.local/share/gateseal`, or in `$XDG_DATA_HOME/gateseal` when that is set. It sets Git's global `init.templateDir` so Git copies the hook into new clones. If you already have a template folder or `core.hooksPath`, setup reports a conflict and changes nothing. It warns if mise is missing or too old. An existing hook stays as `pre-commit.legacy` and runs first.
>
> To undo setup, run `uvx pre-commit uninstall` in each clone. Where `gateseal setup --refresh` added the hook `gateseal-lfs-upload`, also run `git config --local --remove-section hook.gateseal-lfs-upload`. If the clone uses Git LFS, then run `git lfs update`, so that Git LFS writes its push hook again. Then remove the global `init.templateDir` setting and the installation folder, but only if setup created them.
