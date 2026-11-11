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

uv downloads a compatible Python if needed. gateseal accepts CPython 3.14.10
and later 3.14 releases. If your shell cannot find `gateseal`, run
`uv tool update-shell` and open a new shell.

If the repository already has a `gateseal.lock` file, install the version in
its `compiler.version` field. For example, for version `0.1.0`, run:

```sh
uv tool install --python 3.14.10 gateseal==0.1.0
```

Then [activate the worktree](work-in-a-configured-repository.md#prepare-the-clone).
If you work on repositories that use different releases, you can call each
version separately, for example `uvx --python 3.14.10 gateseal@0.1.0 activate`.

## Pin the release in a repository

When you first configure a repository, you record which gateseal wheel each
clone must use. The wheel is the release file whose name ends in `.whl`.
Use the version printed by `gateseal --version`. Once you commit
`gateseal.lock`, contributors can activate their clones without entering the
wheel details.

### Pin the wheel on PyPI

Open the **Download files** page of your release on PyPI, such as
`https://pypi.org/project/gateseal/0.1.0/#files`. Copy the link of the `.whl`
file, and copy its SHA-256 from the file details. Set both in your shell,
replacing the example values:

```sh
GATESEAL_WHEEL_URL='https://files.pythonhosted.org/packages/.../gateseal-0.1.0-py3-none-any.whl'
GATESEAL_WHEEL_SHA256='paste the wheel SHA-256 here'
```

From the root of the repository you want to configure, select the bundled
`hygiene` checks. You can add `--capability python`, `--capability secrets`,
or `--ci github` to this command:

```sh
gateseal configure --capability hygiene \
  --compiler-source "$GATESEAL_WHEEL_URL" --compiler-sha256 "$GATESEAL_WHEEL_SHA256"
```

PyPI never changes a file after upload, and anyone can download it. So every
clone and every GitHub Actions run gets the same wheel, and activation checks
its SHA-256 before it installs it.
[Configure and commit](configure-a-repository.md#configure-and-commit) to
prepare the checks.

### Pin a copy on GitHub

If your team hosts its own copy of the wheel, commit it with its filename to a
GitHub repository that your contributors can read. GitHub Actions needs a
publicly readable copy. Set `GATESEAL_WHEEL_URL` to the wheel's raw GitHub URL
at the full 40-character commit ID, then run the same `configure` command:

```sh
GATESEAL_WHEEL_URL='https://raw.githubusercontent.com/OWNER/REPO/FULL_COMMIT/path/gateseal-0.1.0-py3-none-any.whl'
```

The Lock accepts only these two kinds of URL. Other hosts, and links to GitHub
branches or tags, are unsupported.

### Pin a wheel file on this machine

Download the wheel for your gateseal version from PyPI's **Download files**
page. Keep it outside your repository. Copy the SHA-256 from that wheel's
file details. Set the absolute path and the digest in your shell, replacing
both example values:

```sh
GATESEAL_WHEEL='/absolute/path/gateseal-0.1.0-py3-none-any.whl'
GATESEAL_WHEEL_SHA256='paste the wheel SHA-256 here'
```

In the repository, run:

```sh
gateseal configure --capability hygiene \
  --compiler-wheel "$GATESEAL_WHEEL" --compiler-sha256 "$GATESEAL_WHEEL_SHA256"
```

The Lock records the local path. On another machine, you can activate with
`gateseal activate --compiler-wheel "$GATESEAL_WHEEL"` using a local copy
with the same filename and digest.

## Install a reviewed wheel

If your team supplies a wheel, check it against the SHA-256 in the team's
release record before you install it. In the wheel's directory, replace the
example digest and run:

```sh
echo "REVIEWED_SHA256  gateseal-0.1.0-py3-none-any.whl" | shasum -a 256 -c - &&
  uv tool install --python 3.14.10 ./gateseal-0.1.0-py3-none-any.whl
```

uv installs the wheel only if the digest matches.
