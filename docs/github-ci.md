# Check a repository in GitHub CI

Run the repository's checks on GitHub Actions with the tool versions in
`gateseal.lock`. A pull request checks the proposed merge and can also scan
its history for secrets.

## Turn it on

The runner must be able to download the pinned wheel without credentials.
A local wheel fails with `ci_needs_network_compiler`. For a new repository, follow [Pin the release in a
repository](install.md#pin-the-release-in-a-repository) and add `--ci github`
to the first `configure` command.

For an existing repository, check `compiler.source.kind` in the Lock.
If it is `local-wheel`, switch to the same wheel on PyPI **before** you edit
`gateseal.toml`. Set the [wheel variables](install.md#pin-the-wheel-on-pypi)
with the recorded digest, then change the location:

```sh
gateseal upgrade --compiler-source "$GATESEAL_WHEEL_URL" \
  --compiler-sha256 "$GATESEAL_WHEEL_SHA256"
```

For an existing repository with a public wheel, add this table to
`gateseal.toml`. For a new repository, `--ci github` creates it. Set `default_branch` to the branch whose pushes you want to
check. Pull requests and merge groups can target any branch.

```toml
[ci]
provider = "github"
default_branch = "main"
```

`github` is the only provider. Another value fails with
`unsupported_ci_provider`.

Run these commands to apply the configuration and prepare the Lock. If you
also enabled Python or secrets for the first time, stage `.gateseal` too.

```sh
gateseal configure
gateseal check
gateseal activate
git add gateseal.toml gateseal.lock .pre-commit-config.yaml .github/workflows/gateseal.yml
```

Commit the staged files and open a pull request. Look for the `gateseal`
check on the pull request. A repository administrator can mark this check
as required. To turn it off later, remove `[ci]` and run `gateseal configure`
again. Commit the updated files and the removal of the generated workflow.

## Give the job read access

The bundled policy needs no token. For a Git policy on GitHub, the workflow
uses the job's token by default. That token can read a public policy or one
in the repository that runs the workflow.

If the policy is in another private GitHub repository, set the Actions
secret `GATESEAL_POLICY_TOKEN` to a token with read access to that
repository's contents. The generated Git credential helper supplies the token
only to the policy repository's HTTPS URL. Fork pull requests do not receive
the secret. For SSH URLs and other hosts, the job needs separate Git
credentials. See [Authenticate to a private policy](authenticate.md).

## What the workflow does

The workflow runs on `ubuntu-24.04` with `contents: read`. It checks pushes to
`default_branch`, pull requests, merge groups, manual runs, and a weekly
schedule. The job is named `gateseal-push` on a push and `gateseal` on the
other events.

1. Check out the full Git history without persisting credentials. For a pull
   request, check out GitHub's merge commit. The Lock, workflow, and files
   come from the proposed merge.
2. Download the pinned uv and mise releases and verify them. Install the
   locked gateseal wheel, then run `gateseal activate` to prepare the tools
   and check the repository configuration.
3. Run `gateseal gate` on the files and commits selected by the event.

## The gate

The gate runs hooks selected for the policy's CI stage (pre-commit's
`manual` stage). Every hook runs at every stage, so the gate runs the
same hooks as a commit and `gateseal run`.

| Event | Files checked | History scanned |
| --- | --- | --- |
| Weekly schedule or manual run | All tracked files | None |
| Pull request or merge group (`--base` and `--head`) | Changes from the merge base to the head | Commits after the merge base through the head |
| Fast-forward push (`--before` and `--after`) | Changes between the two commits | Commits after the old head through the new head |
| New branch, rewritten push, or missing old head | All tracked files | Every commit reachable from the new head |
| Branch deletion | None (`nothing_to_check`) | None |

If the base and head have no common ancestor, the gate checks every tracked
file and scans all commits reachable from the head.

When running a gate locally with a revision range, give full commit IDs. Check
out the named head and commit or stash any tracked changes. The gate reads
the worktree and the clone's existing history. A missing required commit
gives `unknown_revision`. A different checked-out head gives
`head_not_checked_out`. A changed index or worktree gives
`uncommitted_changes`.

### History scans

Betterleaks can report a secret in an earlier commit even after a later
commit removes it. A finding includes the commit ID, file, line, column, and
rule. Merge commits are in scope, so a pull request can report a finding
again at the proposed merge. Rotate a real credential and remove it from the
branch's history before merging.

### The result

A passing gate returns `checks_passed` (exit 0). A finding or a hook that
changes a file returns `checks_failed` (exit 1). A tool failure returns
`tool_failed` (exit 4). With `--json`, each hook or finding has stage `ci`.
Each history scan reports its range and the commit for each finding.
See [JSON results](commands-and-results.md#json-results).

## Limits

The workflow runs only on GitHub's `ubuntu-24.04` runner, and you cannot
choose another runner yet. [Tested platforms](tested-platforms.md) lists the
events that the tests covered.
