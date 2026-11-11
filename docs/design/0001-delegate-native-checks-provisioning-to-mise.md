# Delegate native checks provisioning to mise

Accepted on 2026-10-21 and implemented. New repository-checking development in gateseal uses native, repository-pinned tools provisioned by mise and hook environments managed by pre-commit, replacing the planned background checks-container backend. gateseal owns the manifest, reviewed policy inputs, lock, generated configuration, compatibility and deliberate upgrades; delegating installation and isolation avoids owning container lifecycle and Git-index mount translation while preserving repository independence.

Coding agents, authentication, editing and Git commits remain on the host. Configuration remains tracked and activation remains machine-local; neither implicitly upgrades repository pins or installs global host packages. Provisioning must give gateseal's integrity, ownership, staged-index and readiness guarantees.

An optional CI image is separate future work and is not required for contributor activation.
