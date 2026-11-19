# Check a repository in GitHub CI

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

If Python, Pyright or secrets was newly enabled, stage `.gateseal/` too. Review the changes. Commit them together. `gateseal.toml` is the Manifest that selects the policy and checks. `gateseal.lock` is the Lock that records the selection and generated files.

To turn CI off, remove `[ci]`. Run `gateseal configure`. Commit the changes, including the removal of the generated workflow.

## When the workflow runs

The workflow runs for pushes to `default_branch`, pull requests, merge groups, manual runs, and a weekly schedule. See [what the gate checks](ci-gate.md#what-the-gate-checks) for each event.

A new push to a pull request cancels the run for the older commit. Runs of the other events always finish.

## Private policy access

The bundled policy needs no token. The workflow's default GitHub token can read a public policy or one in the same repository. For a policy in another private GitHub repository, add an Actions secret named `GATESEAL_POLICY_TOKEN`. Give the token read access to that repository's contents. Fork pull requests do not receive the secret by default. SSH URLs and other hosts need separate Git credentials. See [Adopt a team policy](adopt-a-team-policy.md#private-policy-access).

For your own workflow, use the policy URL from the base branch. Scope the credential helper to that exact URL so a pull request cannot redirect the token.

The token is for the policy fetch. The runner also needs unauthenticated read access to gateseal's repository and to each selected hook repository.
