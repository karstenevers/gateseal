# Version policy components as catalogs of reviewed versions

Accepted on 2026-10-27 and implemented. Its pre-commit component and the artifact files that policy authors write are superseded by [design record 0006](0006-generate-policy-lock-data-and-ship-the-runtime-with-the-compiler.md).

Policy schema 2 lists each versioned component, such as pre-commit, the pre-commit-hooks repository and Betterleaks, as a catalog: the pinned version and every version the policy's authors reviewed, each with what preparing it needs and the oldest versions of other components it works with. A Version Exception in the Manifest selects one reviewed version instead of the pin, with a reason and a review-by date. The compiler refuses a version the policy did not review, a component the selection does not use, and any combination of releases whose requirements do not hold, including requirements of the hooks the compiler renders. An overdue review date is a warning in `status`, `doctor`, `check` and `configure`; it never drops the exception or disables a check.

Policy Choices are parameters the compiler defines and renders, and a policy exposes one by declaring it with a default. This release defines `hygiene.exclude`, the paths the hygiene hooks skip. Nothing else in the Manifest can change a rule.

A compiler reads the policy schemas it declares and checks `schema` and `minimum_compiler` before it reads any other field. A schema changes for an incompatible format change; within a schema, a later compiler may add an optional field, and a policy that uses one raises `minimum_compiler`. Unknown fields fail.

Two alternatives were rejected. One keeps schema 1 and adds a table of alternative versions beside the fixed pins, which would give upgrade planning two representations to compare. The other fails on an overdue review date, which would let a date change what a commit checks.
