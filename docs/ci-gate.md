# The CI gate

The generated CI file checks each proposed merge with the selected tool versions. You do not have to write or maintain this logic. To turn it on, see [GitHub CI](github-ci.md) or [Azure Pipelines](azure-pipelines.md).

## What each run does

Each run uses a hosted `ubuntu-24.04` machine or a [machine of your own](#machine-requirements), and does these steps:

1. Check out the repository with its full history.
2. Check the machine. Download uv and mise, and check their SHA-256.
3. Install the gateseal release that the Lock pins, and check the generated files against the Lock.
4. Download each selected release binary, such as Betterleaks, and build the hook environments. A download that fails stops the job here, [before any check](#a-failed-download). On GitHub, a push that deleted its branch skips this step.
5. Run the selected CI-stage hooks and repository hooks on the files that the event changed. A hook with the `project` scope, such as Pyright, checks the whole project.
6. Scan the event's commits with each tool that can scan history, such as Betterleaks.

`gateseal.lock` is the Lock. It records the selection and the generated files.

Step 5 includes each hook that runs at `git push`. A push from a clone without the push hook is still checked here. With [Python test checks](python-tests.md#run-the-tests-once-in-ci), step 5 runs the repository's tests when the change has a Python file. Before `tsc` or another tool that reads the Node project, step 5 installs the project's packages from its lockfile.

With the [dependency audit](python-dependencies.md#the-audit), step 5 asks a vulnerability service about each locked package. The service gives live data, not data that the policy pins. So a new advisory can fail a run, such as the weekly run, without any commit.

## What the gate checks

For a pull request on GitHub, the gate finds the merge base of the pull request and its target branch. Azure Pipelines builds the merge of the pull request into its target branch, so the gate there compares that merge commit with the target branch. The gate gives the range to pre-commit as `--from-ref` and `--to-ref`, so the hooks check only the changed files. A hook with the `project` scope, such as Pyright, gets no filenames. It checks the whole project whenever the gate checks files. A project hook that lists `types_or`, such as the test checks, starts only when a changed file has one of those types.

| Event | Files checked | History scanned |
| --- | --- | --- |
| Manual, or weekly with `schedule = true` | All tracked files | None |
| Pull request or merge group on GitHub | Changes from the merge base to the head | Commits after the merge base through the head |
| Pull request on Azure Pipelines | Changes from the target branch to the merge commit | Commits that the target branch lacks, through the merge commit |
| Fast-forward push on GitHub | Changes between the old and new heads | Commits after the old head through the new head |
| Push on Azure Pipelines | All tracked files | Every commit reachable from the new head |
| New branch, rewritten push, or missing old head | All tracked files | Every commit reachable from the new head |
| Branch deletion | None | None |

Azure Pipelines does not tell a build which commit a push replaced. A push build there checks everything, so it cannot miss a commit.

If the base and head have no common ancestor, the gate checks all tracked files. It scans all commits reachable from the head. A selected history-capable tool such as Betterleaks can report a secret removed in a later commit. Rotate a real credential. Remove it from the branch history before merging.

## Machine requirements

A hosted machine meets them. A runner or agent of your own needs:

- Linux on x86-64 with glibc.
- bash, curl, tar, sha256sum, and Git 2.54.0 or later.
- Network access to GitHub for uv, mise, gateseal and the hook repositories.
- Network access to `api.osv.dev` if the repository selects `python-audit`.

On another system or architecture, the first step stops and names this requirement.

The checks of a job run as the user of the runner or agent. Use a machine that keeps state between jobs only for code that you trust. Run pull requests from outside contributors on hosted machines, or on machines that start fresh for each job.

The job keeps its home folder and everything that it installs in the job's temporary folder. So it leaves the home folder and the global Git configuration of the agent's user as they were. Each job installs its tools again, as a run on a hosted machine does.

Git still reads that configuration, such as a proxy setting. Because the job has its own home folder:

- Give each path in that configuration in full, not with `~`.
- The `store` credential helper needs `--file`, and Git can change that file. The `cache` credential helper needs `--socket`.
- OpenSSH still uses `~/.ssh` of the agent's user, and an SSH fetch can add host keys there.

The job ignores the agent's mise settings, the storage folders that the agent sets for pre-commit, uv and mise, and a `SKIP` that would make pre-commit skip hooks.

## Results

A passing gate exits 0. A finding or a hook that changes a file exits 1. A tool failure exits 4. A hook that stops before it can check, such as an audit that cannot reach its service, is a tool failure. See [Commands and results](commands-and-results.md) for JSON results and other errors.

## A failed download

A download can fail when the network or a release page has a problem. The job makes every download in step 4, before the first check, so a failed download never shows as a failed check. gateseal tries each download three times. If the download still fails, the step **Download the locked tools and hook environments** fails, and the job runs no check:

```text
mise could not install betterleaks 1.10.1 from https://github.com/betterleaks/betterleaks/releases/download/v1.10.1/betterleaks_1.10.1_linux_x64.tar.gz in 3 attempts, so the code was not checked.
...
Next: Run the command again, or start the CI job again. If the download fails each time, read the output for the cause.
```

The first line names the archive or the hook repository. The lines after it are the output of mise or pre-commit. The result code is `download_failed`, and the exit code is 4.

Start the job again. If the download fails each time, read the output. For a network error, check the machine's [network access](#machine-requirements). For a wrong checksum or a missing archive, ask the policy author to investigate the upstream release.

`gateseal gate` makes the same downloads before its first hook. A job that starts the gate without step 4 reports the same result.

## What a failed gate shows

In GitHub Actions and Azure Pipelines, a failed gate shows what went wrong, so you do not need a local run to find it:

- **The full log, in color.** The job log shows all the output of each failed hook. gateseal removes recognized credentials, such as tokens, from it first. A workflow command in the output, such as `::error`, `##vso[` or `##[group]`, does not run. A local `gateseal gate` still cuts long output at 6,000 characters. Add `--verbose` to see all of it.
- **Annotations.** Each failed or stopped hook adds an error annotation. When the hook reports a file and line, such as Ruff, the annotation is on that line, and GitHub shows it in the pull request's **Files changed** tab. Other failed hooks annotate the run, and so does a secret that a history scan finds, with its file, line and commit. GitHub shows 10 error annotations of a step, so each failed check gets one before any check gets a second.
- **A run summary.** The summary of the run lists each failed check and the next steps. On Azure Pipelines, it is on the **Extensions** tab of the run.
- **The changes that hooks made.** When a hook changes a file, the log shows the diff of each changed file:

```text
fix end of files.........................................................Failed
- hook id: end-of-file-fixer
- files were modified by this hook

Fixing notes.txt

Changes that the hooks made:
diff --git a/notes.txt b/notes.txt
@@ -1,2 +1,2 @@
 first line
-no final newline
\ No newline at end of file
+no final newline
```

If the worktree had changes before the hooks ran, the log says so and shows no diff. The gate does not commit a change. Run `gateseal run` in a clone, review the change, and commit it.

A passing gate adds no error annotation. Its warnings, such as the [time budget](#time-budget) warning, still show.

## Time budget

Each gate reports how long it took. The time includes the hooks, a test run if the gate has one, and the history scans. It leaves out the downloads of step 4. It still includes the install of the repository's own uv project, which a hook such as Pyright starts. The job log also lists the time of each check and of pre-commit's own work, and names the slowest check. A policy can set `time_budget_seconds`, such as `300` for five minutes. A gate over the budget warns. It still exits as its checks decide, because machine speed changes with load and caches.

The warning names the slowest check and its time. It shows in the job log, on the run's summary page, and in the `--json` result as `over_time_budget`. On GitHub it also shows on the pull request's **Checks** tab. Neither provider sends a notification for a warning in a passing run. To fix a slow gate, speed up its slowest checks or raise the budget.
