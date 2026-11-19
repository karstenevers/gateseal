# The CI gate

The generated CI file checks each proposed merge with the selected tool versions. You do not have to write or maintain this logic. To turn it on, see [GitHub CI](github-ci.md) or [Azure Pipelines](azure-pipelines.md).

## What each run does

Each run uses a new `ubuntu-24.04` machine and does these steps:

1. Check out the repository with its full history.
2. Download uv and mise, and check their SHA-256.
3. Install the gateseal release that the Lock pins, and check the generated files against the Lock.
4. Run the selected CI-stage hooks and repository hooks on the files that the event changed. A hook with the `project` scope, such as Pyright, checks the whole project.
5. Scan the event's commits with each tool that can scan history, such as Betterleaks.

`gateseal.lock` is the Lock. It records the selection and the generated files.

## What the gate checks

For a pull request on GitHub, the gate finds the merge base of the pull request and its target branch. Azure Pipelines builds the merge of the pull request into its target branch, so the gate there compares that merge commit with the target branch. The gate gives the range to pre-commit as `--from-ref` and `--to-ref`, so the hooks check only the changed files. A hook with the `project` scope, such as Pyright, gets no filenames. It checks the whole project whenever the gate checks files.

| Event | Files checked | History scanned |
| --- | --- | --- |
| Manual or weekly | All tracked files | None |
| Pull request or merge group on GitHub | Changes from the merge base to the head | Commits after the merge base through the head |
| Pull request on Azure Pipelines | Changes from the target branch to the merge commit | Commits that the target branch lacks, through the merge commit |
| Fast-forward push on GitHub | Changes between the old and new heads | Commits after the old head through the new head |
| Push on Azure Pipelines | All tracked files | Every commit reachable from the new head |
| New branch, rewritten push, or missing old head | All tracked files | Every commit reachable from the new head |
| Branch deletion | None | None |

Azure Pipelines does not tell a build which commit a push replaced. A push build there checks everything, so it cannot miss a commit.

If the base and head have no common ancestor, the gate checks all tracked files. It scans all commits reachable from the head. A selected history-capable tool such as Betterleaks can report a secret removed in a later commit. Rotate a real credential. Remove it from the branch history before merging.

## Results

A passing gate exits 0. A finding or a hook that changes a file exits 1. A tool failure exits 4. See [Commands and results](commands-and-results.md) for JSON results and other errors.

## Time budget

Each gate reports how long it took. The time includes the hooks, any install of their environments, and the history scans. A policy can set `time_budget_seconds`, such as `300` for five minutes. A gate over the budget warns. It still exits as its checks decide, because machine speed changes with load and caches.

The warning shows in the job log, on the run's summary page, and in the `--json` result as `over_time_budget`. On GitHub it also shows on the pull request's **Checks** tab. Neither provider sends a notification for a warning in a passing run. To fix a slow gate, speed up its slowest checks or raise the budget.
