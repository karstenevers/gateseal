# Policy Lock reference

`policy.lock` records the upstream commits and downloads for every version
your team reviewed. It is part of the [policy release](policy-contract.md#flat-policy).
Generate it in the policy directory and commit it with `policy.toml`.
See [Author a team policy](author-a-policy.md) for the release procedure.

## Format

The lock is UTF-8 JSON with sorted keys, two-space indentation, and a final
newline. `generated_by` is `gateseal policy lock`. Look up a version under
`components.<name>.versions`. For example, the entry for Ruff 0.17.1
contains the full Git commit in its `revision` field.

| Component | What each version records |
| --- | --- |
| Pre-commit repository | `revision` is the full commit ID for the release tag. For an annotated tag, gateseal follows the tag to its commit. |
| Release binary | `platforms` contains the archive `url`, its `sha256`, and the `executable` for each platform in the definition (`linux-x64`, `macos-arm64`, or both). The checksum comes from the release's checksum file. |
| Hook Python | `python` uses uv's download metadata, as in the compiler's `runtime.lock`. Each platform key, such as `cpython-3.15.2-linux-x86_64-gnu`, records the python-build-standalone `build`, the `install_only_stripped` archive's `url`, and its `sha256` from `SHA256SUMS`. |

Built-in and team-defined tools of the same kind use the same lock format.

## Offline check

gateseal checks the lock against the policy every time it loads a release.
To run this check yourself, run `gateseal policy check` from the policy
directory. These errors exit 2 and direct the policy author to run
`gateseal policy lock`.

| Code | What to check |
| --- | --- |
| `policy_lock_missing` | The directory must contain `policy.lock`. |
| `policy_lock_stale` | Every reviewed version needs a record with the platforms the compiler prepares. |
| `policy_lock_unreviewed` | Every component and version in the lock must appear in `policy.toml`. |
| `policy_lock_edited` | Check the JSON format, `generated_by`, field names and values. The URL and executable must match the tool definition. |

The offline check validates the format of revisions and checksums.
`gateseal policy lock` checks those values against upstream. Activation
verifies a downloaded archive against its recorded SHA-256.

## Lock command

Run `gateseal policy lock` in the policy directory when you change the tools
or versions the policy reviews. It checks the existing records against
upstream, fills in missing versions or platforms, and removes versions the
policy no longer reviews. It downloads and verifies each new tool archive.
Only after the new lock passes the offline check does it replace the old one.

Of the policy authoring commands, only `lock` and `add` read upstream release
data. They read tags through Git, and release files over HTTPS using Python's
proxy and certificate settings.

| Code | Exit | Meaning |
| --- | --- | --- |
| `upstream_changed` | 2 | A recorded value changed upstream. The message gives the version and both values. |
| `upstream_tag_missing` | 2 | The repository has no release tag for the reviewed version. |
| `upstream_asset_missing` | 2 | The release lacks its checksum file or an archive listed there. |
| `upstream_checksum_missing` | 2 | The checksum file has no entry for a platform's archive. |
| `upstream_checksum_mismatch` | 2 | The downloaded archive's SHA-256 differs from the published checksum. |
| `upstream_checksums_missing` | 2 | The python-build-standalone build lacks `SHA256SUMS`. |
| `upstream_archive_missing` | 2 | The build lists no Hook Python archive for the requested version on a supported platform. |
| `upstream_unavailable` | 4 | Git or an HTTPS download failed. Check access and rerun the command. A `latest-release.json` that names no build also gives this code, without a next step. |
| `policy_changed` | 2 | A policy file changed while `lock`, `add`, or `pin` was running. The command wrote nothing. Review the edit and retry. |

### Accept a changed fact

Investigate an upstream change before accepting it. If you approve the new
commit or checksum, regenerate the lock and review every change. See
[Handle a fact that upstream changed](author-a-policy.md#handle-a-fact-that-upstream-changed).

### Hook Python versions

For a recorded CPython version, gateseal checks the checksum in the
recorded build's `SHA256SUMS`. For a new version, it uses the build named
by python-build-standalone's `latest-release.json`. That build includes only
the newest patch release of each CPython minor version.

## Add and pin commands

### `gateseal policy add <tool> <version> [<directory>]`

Use `add` to record a version you reviewed. The command prepares a version
table for `policy.toml` and checks all reviewed versions against upstream.
After the checks pass, it writes `policy.toml` and then `policy.lock`.

- If upstream lacks required release data, check that the version has been
  released before you retry.
- If the version is already reviewed, `add` only updates the lock.
- If the command stops between writes, run `gateseal policy lock` to finish.
- The first `gateseal policy add python <version>` also creates
  `[components.python]` after the existing components and pins that version.

### `gateseal policy pin <tool> <version> [<directory>]`

Use `pin` to make a reviewed version the default. The command changes the
component's `pin` in `policy.toml` and checks the result offline.
`policy.lock` records all reviewed versions regardless of which one you pin.

### Edits and codes

Both commands preserve unrelated bytes in `policy.toml`, including comments
and table order. Before writing, the command parses the edited TOML and
checks that only the requested setting changed.

| Code | Exit | What to do |
| --- | --- | --- |
| `invalid_invocation` | 2 | Specify an exact `major.minor.patch` version. |
| `unknown_component` | 2 | Check the component name in `policy.toml`. |
| `version_unreviewed` | 2 | Review the version and run `gateseal policy add` before pinning it. |
| `requires_conflict` | 2 | Check the two versions in the message. Their pins must meet each other's requirements. |
| `policy_edit_unsupported` | 2 | Make the edit by hand. For example, the command cannot insert a version into an inline table. |
