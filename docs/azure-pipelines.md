# Check a repository in Azure Pipelines

The generated pipeline runs [the CI gate](ci-gate.md) for a repository in Azure Repos. A build validation policy makes the gate required, so a pull request with a failed check cannot complete.

## Turn on the gate

For a new repository, [configure it](configure-a-repository.md) with `--ci azure`. For an existing repository, add this table to `gateseal.toml`:

```toml
[ci]
provider = "azure"
default_branch = "main"
```

Set `default_branch` to the branch whose pushes need checks. Run these commands from the repository root:

```sh
gateseal configure
gateseal check
git add gateseal.toml gateseal.lock .pre-commit-config.yaml \
  azure-pipelines-gateseal.yml
```

If `git status` shows new files under `.gateseal/`, stage them too. Review the changes. Commit them together, and push them to `default_branch`.

To turn CI off, remove `[ci]`. Run `gateseal configure`. Commit the changes, including the removal of the generated pipeline file. Then delete the pipeline and the build validation policy in Azure DevOps.

## Create the pipeline

Azure Pipelines does not run a new YAML file by itself. Create the pipeline one time:

1. In the project, open **Pipelines** and select **New pipeline**.
2. Select **Azure Repos Git**, then the repository.
3. Select **Existing Azure Pipelines YAML file** and the path `/azure-pipelines-gateseal.yml`.
4. Open the menu of the **Run** button and select **Save**.
5. Rename the pipeline to `gateseal`.

The pipeline now runs for each push to `default_branch` and when you start it manually. Each push build checks [every tracked file](ci-gate.md#what-the-gate-checks).

## Require the gate for pull requests

In Azure Repos, a branch policy starts the build of a pull request. Add it one time:

1. Open **Project settings**, then **Repositories**, and select the repository.
2. Open **Policies** and select the default branch under **Branch Policies**.
3. Under **Build Validation**, add a policy and select the `gateseal` pipeline.
4. Keep **Trigger** on **Automatic** and **Policy requirement** on **Required**.
5. For **Build expiration**, select **Immediately when the branch is updated**.

To check pull requests to another branch, such as a release branch, add the same policy to that branch.

With this expiration, each pull request is checked against the newest target branch before it completes. Azure DevOps starts no build for a draft pull request.

## Adjust the pipeline

Add keys to `[ci]`, such as `branches`, `schedule` or `timeout_minutes`. Then run `gateseal configure` and commit the changed files. [Manifest and Lock](policy-contract.md#ci) explains each key. Two notes for Azure Pipelines:

- Pull request checks come from the build validation policy, not from `branches`.
- An agent of the `pool` must run Linux on x64 and meet the [machine requirements](ci-gate.md#machine-requirements).

These keys need gateseal 0.3.0 or later. Move the repository to it with `gateseal upgrade` first.

## Run the gate in your own pipeline

To run the gate as a job of your own pipeline, add `standalone = false` and leave out `default_branch`, `branches` and `schedule`:

```toml
[ci]
provider = "azure"
standalone = false
```

`azure-pipelines-gateseal.yml` then holds only the job. Include it under `jobs:` in your pipeline:

```yaml
jobs:
  - template: azure-pipelines-gateseal.yml
```

For a Git policy in Azure Repos, your pipeline also declares the policy repository, because an included file cannot declare resources. Use the name `PROJECT/REPOSITORY` from the policy URL:

```yaml
resources:
  repositories:
    - repository: gateseal_policy
      type: git
      name: PROJECT/REPOSITORY
```

- Your pipeline decides when the gate runs, the order of the jobs, path filters and variable groups. You need no second pipeline and no second build validation policy.
- The gate handles pull request, push, scheduled and manual builds. For other builds it stops with exit code 2. Limit the job to these builds with a condition.
- gateseal does not check your pipeline. If it does not include the file, no gate runs.
- If a pipeline of its own already runs `azure-pipelines-gateseal.yml`, disable or delete that pipeline, and point the build validation policy to your pipeline.

## Private policy access

The bundled policy needs no token. For a Git policy in Azure Repos, write its URL in this form, without the `ORGANIZATION@` part of the clone URL that Azure Repos shows:

```text
https://dev.azure.com/ORGANIZATION/PROJECT/_git/REPOSITORY
```

The first run of the pipeline waits, with the message that the pipeline needs permission to access a resource. Open the run, select **View**, then **Permit**. Later runs do not wait.

The job's token must have read access to the policy repository. It has that access in the project of the pipeline. For a policy in another project of the organization, give access to the build identity of the pipeline's project. Its name is `PROJECT Build Service (ORGANIZATION)`, with the name of the pipeline's project. Do these steps in the project of the policy:

1. Open **Project settings**, then **Permissions**, and add the build identity to the **Readers** group. If the search shows two entries with this name, select the entry whose second line is the ID of the pipeline's project.
2. Open **Project settings**, then **Repositories**, and select the policy repository. On the **Security** tab, add the same identity and set **Read** to **Allow**.

Read access to the repository alone is not sufficient. The identity must also be in the **Readers** group. Microsoft describes the build identities in [Access repositories, artifacts, and other resources](https://learn.microsoft.com/en-us/azure/devops/pipelines/process/access-tokens).

To make Git use a different token, add a secret pipeline variable named `GATESEAL_POLICY_TOKEN`. Give that token read access to the policy repository's code. The run still needs the permission for the resource.

Policies on other hosts and SSH URLs get no token from the pipeline. See [Adopt a team policy](adopt-a-team-policy.md#private-policy-access).

The agent also needs unauthenticated read access to gateseal's repository and to each selected hook repository. A selected tool from npm, such as ESLint, also needs nodejs.org and the npm registry.

## Limits

- Without `pool`, the job runs on the Microsoft-hosted `ubuntu-24.04` image. No key selects another hosted image.
- A Git policy in Azure Repos must be in the organization of the pipeline. Azure Pipelines looks for the repository resource in its own organization. For a policy in another organization, a run does not start, and Azure Pipelines reports that the project or the repository does not exist.
