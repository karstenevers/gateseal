# Try gateseal in a scratch repository

Create a repository and watch a check fix trailing whitespace. Install [gateseal and pre-commit](install.md) first. Git needs a name and email address for the commits. The first commit needs network access.

From a directory outside a repository, run:

```sh
git init -b main gateseal-demo
cd gateseal-demo
gateseal configure --capability hygiene
pre-commit install
git add -A
git commit -m 'Adopt gateseal'
```

Add a file with trailing spaces and try to commit it:

```sh
printf 'notes  \n' > notes.txt
git add notes.txt
git commit -m 'Add notes'
```

The whitespace hook removes the spaces and stops the commit. Review the change. Stage it. Then retry:

```sh
git diff
git add notes.txt
git commit -m 'Add notes'
```

The commit now passes. Remove the scratch directory when you finish.
