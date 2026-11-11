# Acquire Git policy releases by exact commit

Accepted on 2026-10-27 and implemented.

A Manifest names a team's Git policy release by a credential-free `https://` or `ssh://` repository URL, a directory in it, a full commit and the release tag. The Lock records the same identity with the content digest. Only adoption, a release the Lock does not record yet, asks the policy repository whether the tag names the commit; every later command treats the tag as a label. That check also shows the commit belongs to the team's repository, since hosts serve objects from forks by ID.

Only `configure`, `activate`, `check` and `upgrade` acquire a release, and only when the worktree has not stored it. They fetch the one commit into a scratch repository inside the worktree's preparation directory and read the policy's files as exact blobs, so no filter, line-ending conversion or hook applies, and only the files the policy names must be regular, non-executable files. Each worktree stores the content stream under a name that binds commit, directory and digest. Every read recomputes the digest, a damaged copy is preparation that activation repairs, and a Lock whose digest differs from its commit's content is tracked drift that `configure` corrects. Commits, `status`, `doctor` and `run` read only the store and never reach the network.

gateseal never holds a credential. Git authenticates with the user's own configuration, its credential helpers, SSH agent and `url.<base>.insteadOf` rewrites, which apply to the scratch repository as to a clone in the same place. Git runs without a terminal, with prompts disabled and a time limit, so a missing credential fails promptly with a step to try `git ls-remote`. CI supplies a read token for the policy repository through a credential helper scoped to that URL.

Three alternatives were rejected. They are vendoring the policy into each consumer repository, which would make local edits to policy data look legitimate, fetching into the consumer's object store, which mixes object formats and histories, and a store shared by worktrees, which commits would have to reach outside their own preparation for.
