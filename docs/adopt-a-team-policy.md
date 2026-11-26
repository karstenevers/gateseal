# Adopt a team policy

Ask the policy author for the Git repository URL, policy directory, release tag, and capability names. Git needs read access to the policy repository. To publish a policy, see [Author a team policy](author-a-policy.md).

In a repository that already uses the bundled policy, replace the example values. Run:

```sh
gateseal upgrade --policy https://github.com/team/gateseal-policy.git \
  --policy-path policy --policy-tag team-2026.11
```

For a new repository, use `configure` instead. Choose a capability that the team policy offers:

```sh
gateseal configure --capability hygiene \
  --policy https://github.com/team/gateseal-policy.git \
  --policy-path policy --policy-tag team-2026.11
```

At adoption, gateseal checks that the tag points to the selected commit. It records that commit in the Manifest (`gateseal.toml`) and the Lock (`gateseal.lock`). Review both with the generated files. See [an example Manifest](policy-contract.md#example) and [the policy it selects](policy-format.md#example). Commit them together. A moved tag does not change the recorded commit.

To select another release from the same policy, use `gateseal upgrade --policy-tag <tag>`. See [Upgrade and roll back](upgrade.md).

## Private policy access

Keep credentials out of the policy URL. Git uses a credential helper or SSH agent. Check access with the team's actual URL:

```sh
git ls-remote --tags https://github.com/team/gateseal-policy.git
```

The release tag must appear. gateseal fetches without an interactive terminal. Each person who runs gateseal commands needs access when a policy must be fetched. Ordinary commits use the generated configuration and do not fetch the policy.

`status` uses the stored policy without network access. Other repository commands fetch the selected commit if the stored copy is missing or damaged. The server must allow fetching that commit. Keep it reachable from a tag or branch so new clones can fetch it.

For private policies used by CI, see private policy access in [GitHub Actions](github-ci.md#private-policy-access) or [Azure Pipelines](azure-pipelines.md#private-policy-access).
