# Check Python dependencies

The bundled policy can check the dependencies of a uv project. It offers three checks:

| Capability | Hook | Runs | Fails when |
| --- | --- | --- | --- |
| `python-lock` | `uv lock check` | at each commit, in `gateseal run` and in the CI gate | `uv.lock` does not match `pyproject.toml` |
| `python-dependencies` | `deptry` | at each commit, in `gateseal run` and in the CI gate | a dependency is unused, or an import has no dependency |
| `python-audit` | `uv audit` | only in the CI gate | a locked package has a known vulnerability |

Each check reads the whole project. So it runs at each of its stages, also when no Python file changed.

## Select the checks

The repository needs a uv project, with `pyproject.toml` and `uv.lock` at its root. For example:

<!-- python-dependencies-pyproject -->
```toml
[project]
name = "labels"
version = "0"
requires-python = ">=3.12"
dependencies = ["idna==3.10"]

[tool.deptry]
known_first_party = ["labels"]
```

From the repository root, select the checks that you want:

```sh
gateseal configure \
  --capability python-lock \
  --capability python-dependencies \
  --capability python-audit
```

Commit `gateseal.toml`, `gateseal.lock`, `.pre-commit-config.yaml` and `.gateseal/`. A repository that selects only `python` gets none of these checks.

## The lockfile check

The hook runs `uv lock --check`. When you change `pyproject.toml` and do not update `uv.lock`, the commit stops:

```text
The lockfile at `uv.lock` needs to be updated, but `--check` was provided.
To update the lockfile, run `uv lock`.
```

Run `uv lock`, stage `uv.lock`, and commit again.

The hook runs the uv that the policy pins, not the uv on your machine. pre-commit installs that uv from PyPI at the first commit. When `uv.lock` is current, the check needs no installed package and no network, so it is fast.

## deptry

deptry compares the imports in the project with the dependencies in `pyproject.toml`. Like Pyright, it runs in an environment of its own, `.gateseal/environments/deptry`, which holds exactly the packages that `uv.lock` pins. The first commit installs deptry. The hook starts it with `.gateseal/deptry-launcher.txt`, so deptry never reads the files in `.gateseal/`.

The output names each problem:

```text
pyproject.toml: DEP002 'attrs' defined as a dependency but not used in the codebase
src/labels/settings.py:1:8: DEP001 'yaml' imported but missing from the dependency definitions
```

To fix a problem, remove the unused dependency, or add the missing one with `uv add`.

The repository's `[tool.deptry]` settings apply. In a src layout, deptry reads the project's own package as a missing dependency. List the package in `known_first_party`, as in the example above. Use the other settings of deptry for further exceptions, such as `per_rule_ignores`.

In a uv workspace, deptry checks the files of every member against the dependencies of the root `pyproject.toml` only. The root needs a `[project]` table, so deptry cannot check a virtual workspace, whose root has only `[tool.uv.workspace]`. A member's own dependency shows as `DEP003`, a transitive dependency. List such packages for `DEP003` in `per_rule_ignores`:

```toml
[tool.deptry.per_rule_ignores]
DEP003 = ["idna"]
```

## The audit

The hook runs `uv audit` with the uv that the policy pins. It sends the name and version of each locked package to the vulnerability service at `https://api.osv.dev`. It fails when a package has a known vulnerability. The output names the package, the advisory and the fixed version:

```text
idna 3.6 has 1 known vulnerability:

- PYSEC-2024-60: Internationalized Domain Names in Applications (IDNA) vulnerable to denial of service ...

  Fixed in: 3.7
```

The audit runs only in the CI gate, at each run, such as for a pull request or in the weekly run. It never runs at a commit, at a push or in `gateseal run`.

The service gives the current advisories at each run. So a new advisory can fail the weekly run of a repository that nobody changed. Update the package with `uv lock --upgrade-package <name>`, or ask the Policy Author to ignore the advisory.

The audit needs network access to `api.osv.dev`. When the service cannot be reached, the gate fails with `tool_failed` and exit code 4. It never passes. The audit's time counts toward the policy's [time budget](ci-gate.md#time-budget).

### Ignore an advisory

The policy owns the list of ignored advisories. A Policy Author lists each one in `rules/uv-audit.toml` of the policy, by its ID, with the reason in a comment:

```toml
[audit]
ignore = [
  "GHSA-xxxx-xxxx-xxxx",  # The project never decodes untrusted names.
]
```

Any ID of the advisory works, such as its GHSA, PYSEC or CVE ID. The audit reads only this file. So a repository cannot ignore an advisory: the audit does not read `[tool.uv.audit]` in `pyproject.toml` or `[audit]` in `uv.toml`.
