# Try gateseal on a scratch repository

Make a commit that fails a check, review the fix, and commit it again. You
will try the bundled hygiene, Python, and secrets checks in a new repository.

## Install the command

[Install gateseal from PyPI](install.md#install-from-pypi). You need Git, uv,
mise, and network access for the first activation. Git must have a name and
email address to use for the demo commits.

Copy the wheel's URL and SHA-256 for the same release from PyPI, and set
`GATESEAL_WHEEL_URL` and `GATESEAL_WHEEL_SHA256`, as
[Pin the wheel on PyPI](install.md#pin-the-wheel-on-pypi) shows. Use the
`configure` command below for the demo.

## Adopt the policy

From a directory outside any repository, create a new repository:

```sh
git init -b main gateseal-demo
cd gateseal-demo
printf 'def greet(name):\n    return f"Hello, {name}!"\n' > greet.py
```

Select the three capabilities and activate this worktree:

```sh
gateseal configure --capability hygiene --capability python --capability secrets \
  --compiler-source "$GATESEAL_WHEEL_URL" --compiler-sha256 "$GATESEAL_WHEEL_SHA256"
gateseal activate
```

Activation downloads the pinned tools. When it finishes, commit the
configuration and the Python file. The checks should pass:

```sh
git add -A
git commit -m 'Adopt gateseal'
```

## Watch each check stop a commit

### Trailing whitespace

Try to commit a file with two spaces at the end of a line:

```sh
printf 'notes  \n' > notes.txt
git add notes.txt
git commit -m 'Add notes'
```

The `trailing-whitespace` hook removes the spaces from `notes.txt` and stops
the commit. Review the change, stage it, and commit again:

```sh
git diff
git add notes.txt
git commit -m 'Add notes'
```

### Ruff

Try to commit a Python file with an unused import and a misspelled name:

```sh
printf 'import os\ndef shout(name):\n    return nmae.upper()\n' > shout.py
git add shout.py
git commit -m 'Add shout'
```

Ruff removes the unused import and reports `F821 Undefined name` for
`nmae`. Correct the function and commit again:

```sh
printf 'def shout(name):\n    return name.upper()\n' > shout.py
git add shout.py
git commit -m 'Add shout'
```

### A secret

Try to commit a file that contains a sample AWS access key identifier:

```sh
printf 'aws_key = %s%s\n' AKIA Q7XK2M9P4R8T6W3Z > deploy.env
git add deploy.env
git commit -m 'Add deploy settings'
```

Betterleaks reports the location and rule without printing the key:

```text
deploy.env:1:11: aws-access-token (AWS access key identifier)
```

Replace the sample with a region and commit the file:

```sh
printf 'aws_region = eu-central-1\n' > deploy.env
git add deploy.env
git commit -m 'Add deploy settings'
```

## Inspect the result

Run the checks across every tracked file. Each hook reports its result in
the JSON output:

```sh
gateseal run --json
```

Run `gateseal status` to see whether this worktree is ready. Run
`gateseal doctor` to verify the prepared tools and see their versions.
When you finish, you can delete the `gateseal-demo` directory.
