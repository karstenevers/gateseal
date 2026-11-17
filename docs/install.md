# Install gateseal

A developer needs pre-commit, not gateseal. A Repository Maintainer also runs
gateseal, to configure and upgrade a repository. Both work on macOS (arm64)
and Linux (x86-64 with glibc), as [tested platforms](tested-platforms.md)
details.

## What each role installs

- **A developer** needs Git 2.54.0 or later and pre-commit. `gateseal setup`
  installs pre-commit once per machine and needs uv 0.13.6 or later. You can
  also install pre-commit 4.7.0 or later yourself. A policy with a release
  binary, such as Betterleaks, also needs mise 2026.10.19 or later. pre-commit
  builds gateseal's own check from the commit that the Lock pins, so a
  developer never installs gateseal.
- **A Repository Maintainer** needs what a developer needs, and uv 0.13.6 or
  later to run gateseal for `configure` and `upgrade`.

## Run gateseal

uvx runs gateseal without keeping an install:

```sh
uvx gateseal --version
```

To keep the command on your `PATH`, install it:

```sh
uv tool install gateseal
```

gateseal runs on CPython 3.11 and later, and uv downloads one if needed. If
your shell cannot find `gateseal` after the install, run
`uv tool update-shell` and open a new shell.

In a repository that has a `gateseal.lock` file, run the version in its
`gateseal.version` field, for example `uvx gateseal@0.2.0 status`. gateseal
refuses a Lock that pins another release and names `gateseal upgrade`. A
one-off run cannot move the repository to another release by accident.

## Set up pre-commit on this machine

Run `gateseal setup` once per machine. Every clone you make afterwards then
runs pre-commit at `git commit`. In a configured repository, run setup of the
release that `gateseal.lock` pins, as [Run gateseal](#run-gateseal) says:

```sh
uvx gateseal@0.2.0 setup
```

- **pre-commit.** setup installs pre-commit 4.7.0 with uv on Python 3.14, in
  `~/.local/share/gateseal`, or in `$XDG_DATA_HOME/gateseal` when that
  variable is set. A pre-commit that you installed yourself stays as it is,
  and setup adds nothing to your `PATH`.
- **The launcher.** setup writes pre-commit's commit launcher into the
  `git-template` folder there and sets Git's global `init.templateDir` to that
  folder. Every new `git clone` and `git init` on the machine copies the
  launcher. In a repository without `.pre-commit-config.yaml`, the launcher
  says that it skips pre-commit, and the commit goes on.
- **No push launcher.** setup installs only the commit launcher, so
  `git lfs install` still works.

A second run changes nothing. setup also changes nothing, and says why, when
another folder is set. That is Git's `init.templateDir`, the
`GIT_TEMPLATE_DIR` variable, or `core.hooksPath`. For `init.templateDir`, setup
names the file that holds the setting and the command that removes it. To keep
the setting, [use
pre-commit without gateseal setup](#use-pre-commit-without-gateseal-setup).
setup warns when mise is missing or older than 2026.10.19. A Policy with a
release binary, such as Betterleaks, needs it.

### Add the launcher to an existing clone

Git copies the template only into new clones, and it never replaces a hook
that a clone already has. In each clone that you made before setup, run:

```sh
gateseal setup --refresh
```

A hook that the clone already has stays as `.git/hooks/pre-commit.legacy`, and
the launcher runs it first.

### Undo the setup

```sh
git config --global --unset init.templateDir
rm -r ~/.local/share/gateseal
```

Remove `$XDG_DATA_HOME/gateseal` instead when that variable is set. The clones keep
the launcher. Without the pre-commit that setup installed, it runs
a `pre-commit` from your `PATH`, or stops each commit because it finds none.
In those clones, run `pre-commit uninstall` or delete
`.git/hooks/pre-commit`.

## Use pre-commit without gateseal setup

Install pre-commit 4.7.0 or later yourself, for example with
`uv tool install pre-commit`. Then add its commit launcher in each clone:

```sh
pre-commit install
```

pre-commit keeps a hook that the clone already has as
`.git/hooks/pre-commit.legacy`, and runs it first.

This is enough for commits. `gateseal run`, `status` and `doctor` need the
pre-commit that `gateseal setup` installs, and report `needs_setup` or a failed
check without it. To run the checks across every tracked file, use
`pre-commit run --all-files` instead.

### When init.templateDir names your own folder

Run `pre-commit install` in each clone, as above. To give every new clone the
launcher too, add it to your template folder. Do this only when the folder
has no `hooks/pre-commit` file, because the command replaces that file:

```sh
pre-commit init-templatedir -t pre-commit "$(git config --type=path init.templateDir)"
```

### When core.hooksPath is set

Git then runs only the hooks in the folder that `core.hooksPath` names, and
`pre-commit install` refuses to run. Remove the setting, or put pre-commit's
launcher in that folder.

- **Remove it.** `git config --show-origin core.hooksPath` names the file that
  sets it. Delete the setting there, then run `pre-commit install` in each
  existing clone, or set up with `uvx gateseal@0.2.0 setup --refresh`.
- **Keep it.** Run the commands below from the root of a repository. When the
  folder already has a `pre-commit` file, rename it to `pre-commit.legacy`
  first. The launcher runs it before the checks.

```sh
launcher="$(mktemp -d)"
pre-commit init-templatedir -t pre-commit "$launcher"
cp "$launcher/hooks/pre-commit" "$(git config --type=path core.hooksPath)/pre-commit"
```

pre-commit warns that `init.templateDir` does not name the temporary folder,
which does not matter here. Every repository that uses the folder then runs
pre-commit at `git commit`. A repository without `.pre-commit-config.yaml`
skips it.

## Pin the release in a repository

When you first configure a repository, `gateseal configure` pins the gateseal
that runs it: gateseal's repository, `https://github.com/karstenevers/gateseal`,
the version printed by `gateseal --version`, and the commit that the version's
release tag names. configure reads the tag with your Git, so Git's credentials
and `url.<base>.insteadOf` settings apply. From the root of the repository,
run:

```sh
gateseal configure --capability hygiene
```

You can add `--capability python`, `--capability secrets`, or `--ci github`.
The generated workflow installs gateseal from the pinned commit, so every
GitHub Actions run gets the same code.
[Configure and commit](configure-a-repository.md#configure-and-commit) the
generated files.

Once you commit `gateseal.lock`, later commands keep its pin. To move the
repository to another release, run `gateseal upgrade` of that release, such as
`uvx gateseal@0.2.0 upgrade`, as
[Move to another gateseal release](upgrade.md#move-to-another-gateseal-release)
shows.

## Install the pinned commit

To run exactly the code that a Lock pins, install gateseal from the commit in
its `gateseal.commit` field, as the generated workflow does:

```sh
uv tool install \
  "gateseal @ git+https://github.com/karstenevers/gateseal@FULL_COMMIT"
```

uv builds gateseal from that commit. The build tool comes from PyPI by
version.
