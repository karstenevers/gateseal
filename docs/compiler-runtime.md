# Compiler runtime

Each gateseal compiler release specifies the pre-commit version that runs the
hooks, and gateseal and pre-commit run on CPython 3.14. To change pre-commit,
[move to another gateseal release](upgrade.md#move-to-another-gateseal-release).

The compiler package records its runtime in `gateseal/data/runtime.lock`. The
repository Lock pins the gateseal commit, which holds that file.

| Field in `runtime.lock` | Value |
| --- | --- |
| `generated_by` | Command that generated the file. |
| `minimum_uv` | Oldest version of uv that `gateseal setup` accepts. |
| `python` | One download record per supported platform. Each record identifies the CPython version, build, variant, target, archive URL, and SHA-256. |
| `pre_commit` | pre-commit version and the exact packages it needs. Each package record specifies the version, wheel filenames, PyPI URLs, and SHA-256 values. |

`gateseal setup` installs the recorded pre-commit version with uv on CPython
3.14, from the package index that uv is configured for. The generated GitHub
workflow runs gateseal on CPython 3.14 too.
