# gateseal vision

gateseal gives teams a reviewed standard for repository checks. A versioned policy records tool versions, rules, binary checksums, and CI settings. Each repository selects a policy and commits the generated configuration.

For example, an owner can select the bundled file hygiene checks:

```sh
gateseal configure --capability hygiene --ci github
```

See [Configure a repository](docs/configure-a-repository.md) for the complete steps.

## Principles

- A policy defines the shared checks and reviewed versions.
- A repository selects its checks in `gateseal.toml`, the Manifest.
- `gateseal.lock`, the Lock, records the selected inputs and generated files.
- The commit hook and CI check generated files against the Lock.
- pre-commit runs hooks, mise installs selected release binaries, uv runs selected tools in the repository's project, and CI runs the gate.
- The Lock pins direct inputs; it does not freeze every transitive dependency.

The bundled policy includes hygiene, Ruff, Pyright, Python tests with coverage, CRAP scores and mutation tests, and Betterleaks. GitHub Actions and Azure Pipelines are supported. Scheduled discovery of new releases with upgrade pull requests and Windows are planned. See [How gateseal works](docs/architecture.md) for costs and limits.
