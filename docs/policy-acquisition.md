# Policy acquisition reference

Each worktree stores the Git policy release that its repository uses.
The bundled policy ships with the compiler. For private repositories, see
[Give Git access to a team policy](authenticate.md#give-git-access-to-a-team-policy).

## Which commands fetch

| Command | How it reads a Git release |
| --- | --- |
| `configure`, `activate`, `check`, `upgrade` | Use the stored release. Fetch it if the worktree's copy is missing or damaged. |
| `status`, `doctor`, `run`, `gate`, commits | Use only the stored release. These operations never fetch or look up tags or branches. |

## Adoption checks the tag once

[Adoption](../GLOSSARY.md#adoption) occurs when the repository has no
[Lock](../GLOSSARY.md#lock), or when its repository URL, policy path, commit,
or tag differs from the release in the Lock.

At adoption, gateseal uses `git ls-remote` to look up `refs/tags/<tag>` in
the policy repository. The tag must point to the selected commit. For an
annotated tag, gateseal follows the tag to its commit. Checking the tag
matters because some Git hosts also serve objects from forks by commit ID.

| Result | Diagnostic |
| --- | --- |
| The tag points to the selected commit. | `policy_tag` |
| The repository has no such tag. | `policy_tag_missing` |
| The tag points to another commit. | `policy_tag_mismatch` gives the commit the tag points to now. |

Once the Lock records the release, gateseal uses that commit even if the tag
moves.

## Fetch

The Git server must allow a fetch by commit ID and the commit must stay
reachable. Deleting or moving the last tag or branch that reaches a commit
can prevent a fresh clone from fetching its locked policy.

gateseal creates a temporary bare SHA-1 repository named
`.gateseal-<hex>.git` in the worktree's preparation directory. It sets
`remote.origin.url` to the policy URL. Git evaluates its `includeIf` rules
for `gitdir:` and `hasconfig:remote.*.url:` there. The temporary repository
uses its own local configuration.

gateseal fetches the selected commit from `origin` with `--depth=1 --no-tags`.

- Git uses protocol version 2, checks transferred objects, and disables hooks.
- Git runs without a controlling terminal. gateseal sets `GIT_TERMINAL_PROMPT=0`,
  `GCM_INTERACTIVE=never`, and `SSH_ASKPASS_REQUIRE=never`.
- gateseal removes variables that redirect Git to another repository or
  configuration file, such as `GIT_DIR` and `GIT_CONFIG`. It also removes
  variables that trace transfers.
- Each Git command has a 120-second timeout. An HTTP transfer stops if it
  stays below 1,000 bytes per second for 30 seconds.
- Ctrl-C stops Git and its process group.

## Read

gateseal reads `policy.toml`, the files it names, and `policy.lock` from the
selected commit with `git cat-file`. It reads the committed bytes without Git
filters, attributes, or line-ending conversion. It ignores unrelated files,
such as authoring scripts.

The selected policy directory must exist (`policy_path_missing`). Each
policy file must be a regular Git blob with mode `100644`. An executable,
symbolic link, or submodule produces `unsafe_policy_entry`.

## Store

The worktree keeps each release as one file in the `policies` subdirectory
of its preparation directory. The filename combines a SHA-256 of the commit
and policy path with the policy's content digest. gateseal rejects a
symbolic link to the store. It keeps earlier releases and checks the content
digest each time it reads a release.

| Condition | Code | Exit | Next step |
| --- | --- | --- | --- |
| The stored content changed. | `policy_content_mismatch` | 3 | Run `gateseal activate` to fetch the release again. |
| The digest in the Lock differs from the content at the selected commit. | `lock_mismatch` | 2 | Run `gateseal configure` to record the digest of that content. |
| The worktree has no stored copy. | `policy_unavailable` | 3 | Run `gateseal activate`. |

For the two exit-3 conditions, `gateseal status` reports the
[preparation result](commands-and-results.md#commands) `needs_activation`.

## Credentials

Keep credentials out of the policy URL in the [Manifest](../GLOSSARY.md#manifest).
Git uses its credential helpers, SSH agent, and `url.<base>.insteadOf`
rewrites. gateseal does not store or log credentials.

If the fetch fails, gateseal returns `policy_fetch_failed` (exit 4) with
Git's redacted message. Try `git ls-remote <url>` with the policy URL. If
Git cannot list the refs, ask the policy owner for read access. Each
contributor who activates a private policy needs that access.

In CI, scope the credential helper to the exact policy URL. For pull
requests, use the URL from the base branch. See
[Authenticate to a private policy](authenticate.md#in-a-github-actions-workflow).
