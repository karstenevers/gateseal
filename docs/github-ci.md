# Check a repository in GitHub Actions

The generated GitHub Actions workflow runs [the CI gate](ci-gate.md) on the proposed merge. An administrator can require its `gateseal` check before merging.

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

If `git status` shows new files under `.gateseal/`, stage them too. Review the changes. Commit them together.

To turn CI off, remove `[ci]`. Run `gateseal configure`. Commit the changes, including the removal of the generated workflow.

## When the workflow runs

The workflow runs for pushes to `default_branch`, pull requests to any branch, merge groups, and manual runs. See [what the gate checks](ci-gate.md#what-the-gate-checks) for each event.

A new push to a pull request cancels the run for the older commit. Runs of the other events always finish.

A pull request run and a push run check only the changed files. Only a manual run checks every file, for example after an upgrade that brings new rules. To check every file each week, add `schedule = true`.

## Adjust the workflow

Add keys to `[ci]`, then run `gateseal configure` and commit the changed files. For example:

```toml
[ci]
provider = "github"
default_branch = "main"
branches = ["release/*", "develop"]
schedule = true
timeout_minutes = 60
runs_on = ["self-hosted", "linux", "x64"]
```

- `branches` adds branches whose pushes the gate checks. `release/*` covers every branch below `release/`.
- `schedule = true` adds a run every Monday at 04:37 UTC that checks every file. GitHub starts scheduled runs on the repository's default branch only, so keep `default_branch` the same as that branch.
- `timeout_minutes` stops the job after that many minutes. The default is 30. Keep it above the policy's [time budget](ci-gate.md#time-budget), or the budget warning never shows.
- `runs_on` selects the runner by its labels, such as your own runners or a larger runner. The runner must meet the [machine requirements](ci-gate.md#machine-requirements).

These keys need gateseal 0.3.0 or later. Move the repository to it with `gateseal upgrade` first. See [Manifest and Lock](policy-contract.md#ci) for every key.

## Run the gate in your own workflow

To run the gate as a job of your own workflow, add `standalone = false` and leave out `default_branch`, `branches` and `schedule`:

```toml
[ci]
provider = "github"
standalone = false
```

The generated workflow then runs only when another workflow calls it. Call it from a job of your workflow:

```yaml
jobs:
  gateseal:
    uses: ./.github/workflows/gateseal.yml
    secrets: inherit
```

- Your workflow decides when the gate runs, the order of the jobs, path filters and concurrency.
- The gate handles branch pushes, pull requests, merge groups, schedules and manual runs. For other events it stops with exit code 2. Limit the job to these events with an `if:` condition, and leave out tag pushes, such as with `github.ref_type == 'branch'` for a push.
- The check name is your job's name, a slash, and `gateseal`, such as `gateseal / gateseal`. Require that name in the branch rules.
- gateseal does not check your workflow. If no job calls the file, no gate runs.

## Private policy access

The bundled policy needs no token. The workflow's default GitHub token can read a public policy or one in the same repository. For a policy in another private GitHub repository, add an Actions secret named `GATESEAL_POLICY_TOKEN`. Give the token read access to that repository's contents. Fork pull requests do not receive the secret by default. A workflow that calls the gate passes the secret with `secrets: inherit`. SSH URLs and other hosts need separate Git credentials. See [Adopt a team policy](adopt-a-team-policy.md#private-policy-access).

For your own workflow, use the policy URL from the base branch. Scope the credential helper to that exact URL so a pull request cannot redirect the token.

The token is for the policy fetch. The runner also needs unauthenticated read access to gateseal's repository and to each selected hook repository. A selected tool from npm, such as ESLint, also needs nodejs.org and the npm registry. A tool that reads the Node project, such as `tsc`, also needs the registry of the project's packages.
