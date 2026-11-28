# The CI gate

The generated CI file checks each proposed merge with the selected tool versions. You do not have to write or maintain this logic. To turn it on, see [GitHub Actions](github-ci.md) or [Azure Pipelines](azure-pipelines.md).

## What each run does

Each run uses a hosted `ubuntu-24.04` machine or a [machine of your own](#machine-requirements), and does these steps:

1. Check out the repository with its full history.
2. Check the machine. Download uv and mise, and check their SHA-256.
3. Install the gateseal release that the Lock (`gateseal.lock`) pins, and check the generated files against the Lock.
4. Download each selected release binary, such as Betterleaks, and build the hook environments. A download that fails stops the job here, [before any check](#a-failed-download).
5. Run the selected hooks and repository hooks on the files that the event changed. This includes each hook that runs at `git push`.
6. Scan the event's commits with each tool that can scan history, such as Betterleaks.

A dependency audit, such as [`python-audit`](built-in-tools.md), asks a vulnerability service about each locked package. The service gives live data. So a new advisory can fail a run, such as the weekly run, without any commit.

## What the gate checks

The gate gives pre-commit the range of the event, so the hooks check only the changed files. A hook with the `project` scope, such as Pyright, checks the whole project. A project hook that lists file types, such as the test checks, starts only when a changed file has one of those types.

| Event | Files checked | History scanned |
| --- | --- | --- |
| Manual, or weekly with `schedule = true` | All tracked files | None |
| Pull request or merge group on GitHub | Changes from the merge base to the head | Commits after the merge base through the head |
| Pull request on Azure Pipelines | Changes from the target branch to the merge commit | Commits that the target branch lacks, through the merge commit |
| Fast-forward push on GitHub | Changes between the old and new heads | Commits after the old head through the new head |
| Push on Azure Pipelines | All tracked files | Every commit reachable from the new head |
| New branch, rewritten push, or missing old head | All tracked files | Every commit reachable from the new head |
| Branch deletion | None | None |

Azure Pipelines does not tell a build which commit a push replaced. So a push build there checks everything.

A history scan can report a secret that a later commit removed. Rotate a real credential, and remove it from the branch history before you merge.

## Machine requirements

A hosted machine meets them. A runner or agent of your own needs:

- Linux on x86-64 with glibc.
- bash, curl, tar, sha256sum, and Git 2.54.0 or later.
- Network access to GitHub for uv, mise, gateseal and the hook repositories.
- Network access to `api.osv.dev` if the repository selects `python-audit` or `typescript-audit`. Behind a proxy, set `HTTPS_PROXY` in the job.

On another system or architecture, the first step stops and names this requirement.

The checks of a job run as the user of the runner or agent. Use a machine that keeps state between jobs only for code that you trust. Run pull requests from outside contributors on hosted machines, or on machines that start fresh for each job.

The job keeps its home folder and everything that it installs in the job's temporary folder. So each job installs its tools again, and gateseal writes nothing to the agent user's home folder. Git still reads that user's global configuration, such as a proxy setting. Give each path in that configuration in full, not with `~`. A Git credential helper and OpenSSH can still write to that user's files.

## Results

A passing gate exits 0. A finding or a hook that changes a file exits 1. A tool failure exits 4. A hook that stops before it can check, such as an audit that cannot reach its service, is a tool failure. See [Commands and results](commands-and-results.md) for JSON results and other errors.

## A failed download

The job makes every download in step 4, before the first check. So a failed download never shows as a failed check. gateseal tries each download three times. Then the step **Download the locked tools and hook environments** fails, and the job runs no check:

```text
mise could not install betterleaks 1.10.1 from https://github.com/betterleaks/betterleaks/releases/download/v1.10.1/betterleaks_1.10.1_linux_x64.tar.gz in 3 attempts, so the code was not checked.
...
Next: Run the command again, or start the CI job again. If the download fails each time, read the output for the cause.
```

The result code is `download_failed`, and the exit code is 4. Start the job again. If the download fails each time, read the output of mise or pre-commit below the first line. For a network error, check the machine's [network access](#machine-requirements). For a wrong checksum or a missing archive, ask the policy author to investigate the upstream release.

## What a failed gate shows

In GitHub Actions and Azure Pipelines, a failed gate shows what went wrong, so you do not need a local run to find it:

- **The full log, in color.** The job log shows all the output of each failed hook. gateseal removes recognized credentials, such as tokens, from it first.
- **Annotations.** Each failed or stopped hook adds an error annotation. When the hook reports a file and line, such as Ruff, the annotation is on that line. A secret that a history scan finds gets one too.
- **A run summary.** The summary of the run lists each failed check and the next steps. On Azure Pipelines, it is on the **Extensions** tab of the run.
- **The changes that hooks made.** When a hook changes a file, the log shows the diff of each changed file.

The gate does not commit a change. Run `gateseal run` in a clone, review the change, and commit it.

A local `gateseal gate` cuts long output at 6,000 characters. Add `--verbose` to see all of it.

## Time budget

Each gate reports how long it took, and the job log names the slowest check. The time includes the hooks, the test runs and the history scans. It leaves out the downloads of step 4.

A policy can set `time_budget_seconds`, such as `300` for five minutes. A gate over the budget warns. It still exits as its checks decide, because machine speed changes with load and caches.

The warning names the slowest check and its time. It shows in the job log, on the run's summary page, and in the `--json` result as `over_time_budget`. On GitHub it also shows on the pull request's **Checks** tab. Neither provider sends a notification for a warning in a passing run. To fix a slow gate, speed up its slowest checks or raise the budget.
