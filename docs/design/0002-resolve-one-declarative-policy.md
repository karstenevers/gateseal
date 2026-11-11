# Resolve one declarative Policy

Accepted on 2026-10-21 and implemented. [Design record 0007](0007-implement-tool-kinds-in-the-compiler-and-describe-every-tool-as-data.md) refines its clause that capabilities remain implemented by the compiler: the compiler implements tool kinds, and every tool is a declarative definition of one.

Each consumer repository resolves exactly one flat Policy release. Derivation from a reference policy belongs to policy authoring, avoiding runtime inheritance, merge precedence and multi-layer upgrade conflicts; a policy contains declarative metadata and rule files, while capabilities remain implemented by the compiler rather than loaded as plugins.

A Git policy release is identified by its immutable commit and content digest. A release tag is a human label whose target is checked when adopting an upgrade, not a source of truth consulted during ordinary execution. The package also carries a reference policy snapshot for configuration and demonstration without a policy Git fetch; that snapshot is identified by its version, content digest and containing package artifact, not a mutable remote reference. A policy has its own schema version and minimum compiler requirement, and compilers declare supported policy schemas.

The Manifest separates exposed Policy Choices from Version Exceptions. Choices select supported policy parameters; exceptions change named component pins and carry a reason and review-by date, remain visible in status and doctor, and are revalidated during upgrades. Arbitrary rule overrides do not belong in the manifest. A different rule requires an exposed choice or a policy change.
