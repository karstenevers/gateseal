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
its `compiler.version` field. For example, for version `0.1.0`, run:

```sh
uv tool install --python 3.14.10 gateseal==0.1.0
```

Then [activate the worktree](work-in-a-configured-repository.md#prepare-the-clone).
If you work on repositories that use different releases, you can call each
version separately, for example `uvx --python 3.14.10 gateseal@0.1.0 activate`.

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

When you first configure a repository, you record which gateseal wheel each
clone must use. The wheel is the release file whose name ends in `.whl`.
Use the version printed by `gateseal --version`. Once you commit
`gateseal.lock`, contributors can activate their clones without entering the
wheel details.

### Pin the wheel on PyPI

On the **Download files** page of your release on PyPI, such as
`https://pypi.org/project/gateseal/0.1.0/#files`, copy the link of the `.whl`
file, and copy its SHA-256 from the file details. From the root of the
repository, run this command with the two values in place of `PYPI_WHEEL_URL`
and `WHEEL_SHA256`:

```sh
gateseal configure --capability hygiene \
  --compiler-source PYPI_WHEEL_URL --compiler-sha256 WHEEL_SHA256
```

You can add `--capability python`, `--capability secrets`, or `--ci github`.
PyPI never changes a file after upload, so every clone and every GitHub
Actions run gets the same wheel. Activation checks its SHA-256 before it
installs it. [Configure and commit](configure-a-repository.md#configure-and-commit)
to prepare the checks.

### Pin a copy on GitHub

If your team hosts its own copy of the wheel, commit it with its filename to a
GitHub repository that your contributors can read. GitHub Actions needs a
publicly readable copy. In the same command, use the wheel's raw GitHub URL at
the full 40-character commit ID in place of `PYPI_WHEEL_URL`:

```text
https://raw.githubusercontent.com/OWNER/REPO/FULL_COMMIT/path/gateseal-0.1.0-py3-none-any.whl
```

The Lock accepts only these two kinds of URL. Other hosts, and links to GitHub
branches or tags, are unsupported.

### Pin a wheel file on this machine

Download the wheel for your gateseal version from PyPI's **Download files**
page, and copy its SHA-256 from the file details. Keep the wheel outside your
repository. In the repository, run this command with the wheel's absolute path
and its SHA-256 in place of the example path and `WHEEL_SHA256`:

```sh
gateseal configure --capability hygiene \
  --compiler-wheel '/absolute/path/gateseal-0.1.0-py3-none-any.whl' \
  --compiler-sha256 WHEEL_SHA256
```

The Lock records the local path. On another machine, run
`gateseal activate --compiler-wheel` with the absolute path of a copy that has
the same filename and digest.

## Install a reviewed wheel

If your team supplies a wheel, check it against the SHA-256 in the team's
release record before you install it. In the wheel's directory, replace the
example digest and run:

```sh
echo "REVIEWED_SHA256  gateseal-0.1.0-py3-none-any.whl" | shasum -a 256 -c - &&
  uv tool install --python 3.14.10 ./gateseal-0.1.0-py3-none-any.whl
```

uv installs the wheel only if the digest matches.
