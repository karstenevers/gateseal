# Check a repository in GitHub CI

The generated GitHub Actions workflow checks the proposed merge with the selected tool versions. An administrator can require its `gateseal` check before merging.

For a new repository, [configure it](configure-a-repository.md) with `--ci github`. For an existing repository, add this table to `gateseal.toml`:

```toml
[ci]
provider = "github"
default_branch = "main"
```

Set `default_branch` to the branch whose pushes need checks. Run these commands from the repository root:

```sh
gateseal configure
gateseal check
git add gateseal.toml gateseal.lock .pre-commit-config.yaml \
  .github/workflows/gateseal.yml
```

If Python or secrets was newly enabled, stage `.gateseal/` too. Review the changes. Commit them together. `gateseal.toml` is the Manifest that selects the policy and checks. `gateseal.lock` is the Lock that records the selection and generated files.

To turn CI off, remove `[ci]`. Run `gateseal configure`. Commit the changes, including the removal of the generated workflow.

## Private policy access

The bundled policy needs no token. The workflow's default GitHub token can read a public policy or one in the same repository. For a policy in another private GitHub repository, add an Actions secret named `GATESEAL_POLICY_TOKEN`. Give the token read access to that repository's contents. Fork pull requests do not receive the secret by default. SSH URLs and other hosts need separate Git credentials. See [Adopt a team policy](adopt-a-team-policy.md#private-policy-access).

For your own workflow, use the policy URL from the base branch. Scope the credential helper to that exact URL so a pull request cannot redirect the token.

The token is for the policy fetch. The runner also needs unauthenticated read access to gateseal's repository and to each selected hook repository.

## What the gate checks

The workflow runs on `ubuntu-24.04` for pushes to `default_branch`, pull requests, merge groups, manual runs, and a weekly schedule. Each run does these steps:

1. Check out the repository with its full history.
2. Download uv and mise, and check their SHA-256.
3. Install the gateseal release that the Lock pins, and check the generated files against the Lock.
4. Run the selected CI-stage hooks and repository hooks on the files that the event changed.
5. Scan the event's commits with each tool that can scan history, such as Betterleaks.

You do not have to write or maintain this logic. For a pull request, the gate finds the merge base of the pull request and its target branch. It gives that range to pre-commit as `--from-ref` and `--to-ref`, so the hooks check only the changed files. A weekly or manual run uses `--all-files`.

A new push to a pull request cancels the run for the older commit. Runs of the other events always finish.

| Event | Files checked | History scanned |
| --- | --- | --- |
| Manual or weekly | All tracked files | None |
| Pull request or merge group | Changes from the merge base to the head | Commits after the merge base through the head |
| Fast-forward push | Changes between the old and new heads | Commits after the old head through the new head |
| New branch, rewritten push, or missing old head | All tracked files | Every commit reachable from the new head |
| Branch deletion | None | None |

If the base and head have no common ancestor, the gate checks all tracked files. It scans all commits reachable from the head. A selected history-capable tool such as Betterleaks can report a secret removed in a later commit. Rotate a real credential. Remove it from the branch history before merging.

A passing gate exits 0. A finding or a hook that changes a file exits 1. A tool failure exits 4. See [Commands and results](commands-and-results.md) for JSON results and other errors.

## Time budget

Each gate reports how long it took. The time includes the hooks, any install of their environments, and the history scans. A policy can set `time_budget_seconds`, such as `300` for five minutes. A gate over the budget warns. It still exits as its checks decide, because runner speed changes with load and caches.

The warning shows in the job log, as an annotation on the run's summary page and on the pull request's **Checks** tab, and in the `--json` result as `over_time_budget`. GitHub sends no notification for a warning in a passing run. To fix a slow gate, speed up its slowest checks or raise the budget.
