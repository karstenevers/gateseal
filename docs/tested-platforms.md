# Tested platforms

gateseal supports **macOS on arm64** and **Linux on x86-64 with glibc**.
Use [Install gateseal](install.md) to set up a supported machine.

## Prerequisite floors

| Tool | Version | When you need it |
| --- | --- | --- |
| Git | 2.54.0 or later | Repository commands and commits. gateseal uses `git hook list` to detect other configured hooks. |
| uv | 0.13.6 or later is tested. The runtime accepts 0.13.5. | Install and prepare gateseal. A prepared commit can run after you remove uv. |
| mise | 2026.10.19 or later | Prepare release binaries such as Betterleaks. The bundled policy requires mise when you select `secrets`. |

Activation prepares CPython and pre-commit. You do not need to install either
separately. An older Git, uv, or mise produces `git_version_mismatch`,
`uv_version_mismatch`, or `mise_version_mismatch`, respectively.

## Recorded test environments

| | macOS | Linux |
| --- | --- | --- |
| System | macOS 27.2 (26B123), arm64 | GitHub `ubuntu-24.04` image 20261025.353.1, x86-64 glibc |
| Git | 2.54.0 (Apple Git-157) | 2.56.0 |
| uv | 0.13.6 | 0.13.6 |
| mise | 2026.10.25 | 2026.10.19 |

Both environments ran the full gateseal 0.1.0 suite with CPython 3.14.10
build 20261026 and pre-commit 4.7.0. Tests used the bundled pre-commit-hooks
6.0.0, Ruff 0.17.1, and Betterleaks 1.9.0. A version exception also exercised
Betterleaks 1.8.0. See [What the tests prove](what-the-tests-prove.md).

The macOS end-to-end guide runs used a local wheel and scratch repositories.
The Linux guide run on 2026-10-28 used the earlier CPython 3.13.17 runtime
and predates the CI and upgrade guides. Separate generated-workflow tests on
GitHub's Linux runner prepared and checked two consumer repositories, one
with the bundled policy and one with a Git policy.

## Platform limits

Native Windows is unsupported. WSL, Intel Mac, Linux arm64, and Linux with
musl are untested. The generated GitHub Actions workflow runs only on GitHub's
`ubuntu-24.04` runner, and you cannot choose another runner yet. The suite
also does not cover Git submodules.
