# Security policy

## Supported versions

gateseal is before 1.0, and only the latest release receives security fixes.

## Report a vulnerability

Report it privately through GitHub. On the repository's **Security** tab,
choose **Report a vulnerability**, or open the
[advisory form](https://github.com/karstenevers/gateseal/security/advisories/new)
directly. Please do not open a public issue or pull request.

Include the gateseal version, your platform, what an attacker gains, and the
steps that reproduce it. The maintainer handles reports on a best-effort
basis. Once a fix is released, the advisory is published and credits you,
unless you prefer not to be named.

## Scope

gateseal pins the compiler, the policy and every tool in a lock, and runs only
what activation prepared from it. These are in scope:

- A way to make gateseal fetch, prepare or run anything other than what the
  lock records.
- A way for a gateseal command or a generated file to expose a credential.

Report a vulnerability in pre-commit, uv, mise, Ruff, Betterleaks or another
tool that gateseal drives to that project. A policy release is reviewed input,
so a tool version that its authors approved is not a gateseal vulnerability.
