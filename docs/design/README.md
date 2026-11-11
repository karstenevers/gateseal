# Design records

Each record captures one decision behind gateseal's design: the problem, the
choice, and the options it rejected. A record stays as it was accepted. A later
record that changes it says so in its first paragraph, and the older record
links forward to it.

| Record | Accepted | Decision |
| --- | --- | --- |
| [0001](0001-delegate-native-checks-provisioning-to-mise.md) | 2026-10-21 | Run native, pinned tools through mise and pre-commit instead of a checks container. |
| [0002](0002-resolve-one-declarative-policy.md) | 2026-10-21 | Each repository resolves exactly one flat, declarative policy release, with no runtime inheritance. |
| [0003](0003-prepare-gateseal-tools-before-commits.md) | 2026-10-21 | Activation prepares every tool in advance, and a commit runs only what activation prepared. |
| [0004](0004-acquire-git-policy-releases-by-exact-commit.md) | 2026-10-27 | A team's Git policy release is pinned by exact commit and content digest, never by tag. |
| [0005](0005-version-policy-components-as-reviewed-catalogs.md) | 2026-10-27 | A policy lists each component's pinned version beside every version its authors reviewed. |
| [0006](0006-generate-policy-lock-data-and-ship-the-runtime-with-the-compiler.md) | 2026-10-27 | Policy authors never type a hash, and each compiler release ships its own Python and pre-commit. |
| [0007](0007-implement-tool-kinds-in-the-compiler-and-describe-every-tool-as-data.md) | 2026-10-27 | The compiler implements a few tool kinds, and every tool is data of one kind. |
| [0008](0008-generate-the-ci-gate-as-an-owned-workflow.md) | 2026-11-01 | CI runs the same Lock through a generated workflow that the compiler plans and owns. |
| [0009](0009-upgrade-through-one-planner-and-let-the-candidate-render-a-compiler-move.md) | 2026-11-01 | One planner handles every upgrade and rollback, and the new compiler renders a compiler move. |
| [0010](0010-share-one-sealed-interpreter-store-across-repositories.md) | 2026-11-02 | All repositories on a machine share one sealed copy of each Python interpreter. |
| [0011](0011-share-sealed-hook-environments-through-the-interpreter-store.md) | 2026-11-05 | Each hook environment is built once per machine in the interpreter store and shared by every repository. |
| [0012](0012-fail-gateseal-pull-requests-on-surviving-mutants.md) | 2026-11-09 | A pull request fails while a mutant survives in its changed lines or those lines score below 90%. |
