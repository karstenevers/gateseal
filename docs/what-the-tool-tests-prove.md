# Tests for policies and tools

The integration tests use the installed gateseal command to adopt team
policies and commit files through Ruff and Betterleaks. The following results
describe the bundled definitions and the conditions the tests exercise.

## Git policies and policy authoring

- Adoption verifies that a tag names the selected commit. Moving the tag
  later leaves the adopted repository on its locked release.
- A real commit uses the policy choices and version exceptions in its
  Manifest. Unsafe policy trees and content that differs from the Lock are
  rejected.
- Tests for `gateseal policy lock` use annotated and lightweight Git tags,
  release checksums, and archives. A moved tag, replaced checksum, or
  missing platform record cannot silently replace a reviewed fact.
- `policy add` and `policy pin` preserve other settings and comments in
  `policy.toml`. An unreviewed version or incompatible requirement prevents
  a pin.
- Separate network tests resolve the bundled versions from their upstream
  releases and compare the resulting `policy.lock` with the bundled file.

## Python and Ruff

The bundled policy pins the Ruff hook repository to a Git commit. That
repository requires an exact Ruff package version from the configured
package index. The hook environment has no locked wheel hash for Ruff.
Activation and `gateseal doctor` check the installed version and exercise
both linting and formatting with the owned configuration.

| Test condition | Observed result |
| --- | --- |
| Undefined name | The hook reports the file, line, column, and rule and stops the commit. |
| Safe fix or formatting change | The hook edits the file and stops the commit. The restaged file passes. A finding with only an unsafe fix leaves the file untouched. |
| Repository or nested Ruff settings that ignore the tested rules | The hooks still use the owned configuration. The tested `RUFF_` output overrides do not change the result. |
| Filename `--exit-zero.py` | Ruff checks it as a file. |
| Partially staged source file | The unstaged edit survives a passing run, a finding, and a reformat. |
| Missing executable permission on prepared Ruff, with a host Ruff available | The commit stops before pre-commit starts. Doctor requests activation, which repairs it. |

`gateseal run --json` reports skipped Ruff hooks for a selection with no
Python files. With applicable files, it reports structured findings,
including the cell for a notebook finding.

## Secrets and mise

Activation uses mise to install the locked Betterleaks binary for the current
platform. The tests cover both the installer and the commit hook.

| Test condition | Observed result |
| --- | --- |
| A staged test key, two keys on one line, or a token under a JSON `"api_key"` field | The commit stops and reports the finding without printing the key values. |
| A key only in the unstaged part of a file | The staged commit passes and the unstaged edit survives. `gateseal run` sees the working file and reports the key. |
| `git commit -a` includes that key | The commit stops and the original index survives. |
| Missing prepared binary with fake binaries first on `PATH` | A commit stops before pre-commit starts. Doctor and run request activation. |
| mise removed after activation | Commits, run, status, and doctor still work. |
| A failed mise install or a binary with the wrong version | The previous attempt stays current. |
| Reused cache or a wrong archive checksum | A second clone can activate with a warm cache and mise offline. A checksum mismatch prevents installation. |
| Project or user mise configuration with executable hooks, and environment variables pointing to a fake binary | Activation runs no planted scripts and selects the verified binary inside the attempt. |

mise installs through its `http` backend with a locked URL and checksum.
Activation sets its data, state, and system data directories inside the
attempt and passes `--no-config`. The commit hook starts the prepared
Betterleaks binary directly. See [What gateseal trusts](architecture.md#what-gateseal-trusts).
