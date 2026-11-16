# Install gateseal

Use uv to install the gateseal command on macOS (arm64) or Linux (x86-64 with
glibc). You need Git 2.54.0 or later and uv 0.13.6 or later. To run the
bundled secrets check, you also need mise 2026.10.19 or later.
See [tested platforms](tested-platforms.md) for details.

## Install from PyPI

```sh
uv tool install --python 3.14.10 gateseal
gateseal --version
```

uv downloads a compatible Python if needed. gateseal runs on CPython 3.11 and
later. Release 0.1.0 needs CPython 3.14.10 or a later 3.14 release, which the
`--python` flag selects. If your shell cannot find `gateseal`, run
`uv tool update-shell` and open a new shell.

If the repository already has a `gateseal.lock` file, install the version in
its `gateseal.version` field. For example, for version `0.1.0`, run:

```sh
uv tool install --python 3.14.10 gateseal==0.1.0
```

Then [set up your machine](work-in-a-configured-repository.md#set-up-your-machine).
If you work on repositories that use different releases, you can call each
version separately, for example `uvx --python 3.14.10 gateseal@0.1.0 status`.

## Set up pre-commit on this machine

Run `gateseal setup` once per machine. Every clone you make afterwards then
runs pre-commit at `git commit`:

```sh
gateseal setup
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

A second run changes nothing. When `init.templateDir` already names another
folder, setup changes nothing and names the file that sets it and the command
that removes the setting. Keep what you need from the folder, run that command,
then run `gateseal setup` again. setup refuses the same way when the
`GIT_TEMPLATE_DIR` variable is set to another folder or to an empty value, or
when Git's `core.hooksPath` is set, because Git would then skip the launcher.
setup also warns when mise is missing or older than 2026.10.19, because a
Policy with a release binary, such as Betterleaks, needs it.

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
rm -r ~/.local/share/gateseal/tools \
  ~/.local/share/gateseal/bin \
  ~/.local/share/gateseal/git-template
```

Remove the `tools`, `bin` and `git-template` folders under
`$XDG_DATA_HOME/gateseal` instead when that variable is set. The clones keep
the launcher. Without the pre-commit that setup installed, it runs
a `pre-commit` from your `PATH`, or stops each commit because it finds none.
In those clones, run `pre-commit uninstall` or delete
`.git/hooks/pre-commit`.

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
repository to another release, install that release and run
`gateseal upgrade`, as
[Move to another gateseal release](upgrade.md#move-to-another-gateseal-release)
shows.

## Install the pinned commit

To run exactly the code that a Lock pins, install gateseal from the commit in
its `gateseal.commit` field, as the generated workflow does:

```sh
uv tool install --python 3.14.10 \
  "gateseal @ git+https://github.com/karstenevers/gateseal@FULL_COMMIT"
```

uv builds gateseal from that commit. The build tool comes from PyPI by
version.
