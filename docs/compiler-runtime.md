# Compiler runtime

Each gateseal compiler release specifies the CPython that runs the compiler
and the pre-commit version that runs the hooks. To change either, use a
[compiler target](upgrade.md#compiler-targets) with `gateseal upgrade`.
A policy can select a separate [Hook Python](policy-format.md#hook-python)
for Python-language hooks.

The compiler package records its runtime in `gateseal/data/runtime.lock`.
The repository Lock records the same installation inputs so you can review
them during an upgrade.

| Field in `runtime.lock` | Value |
| --- | --- |
| `generated_by` | Command that generated the file. |
| `minimum_uv` | Oldest version of uv that activation accepts. |
| `python` | One download record per supported platform. Each record identifies the CPython version, build, variant, target, archive URL, and SHA-256. |
| `pre_commit` | pre-commit version and the exact packages it needs. Each package record specifies the version, wheel filenames, PyPI URLs, and SHA-256 values. |

Activation gives uv the Python record for the current machine. It installs
pre-commit in a separate environment and requires the recorded wheel hashes.
