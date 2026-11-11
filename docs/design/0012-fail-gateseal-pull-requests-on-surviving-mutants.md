# Fail gateseal pull requests on surviving mutants

Accepted on 2026-11-09.

gateseal's tests should be strong when they are written, not weak and improved afterwards. So [pytestmut](https://github.com/martincjespersen/pytestmut) mutates `src/gateseal` and runs the unit layer. `mutation-diff` runs it with `--diff` against the base of every pull request. The job fails on any survivor in the changed lines, because `max_survivors = 0`. It also fails when the changed lines score below `min_score`, which is 90%. A mutant that no unit test reaches counts as missed, so code that only the integration layer runs lowers the score. The author kills a survivor with a test, or marks the line of an equivalent mutant with the operator and the reason. Code that only a download reaches leaves the score through an entry in `[tool.pytestmut.suppress]`, with a comment that gives its reason.

A mutation-score gate is often rejected because reviewers at Google acted on only 38% of the survivors shown to them. There a person triages mutants in someone else's change. Here the author of the change closes that change's survivors in the same pull request, before review. The gate covers only the changed lines, so an old survivor never blocks unrelated work.

## Considered Options

- Fail on any survivor in a pull request's changed lines
- Report survivors without failing
- Fail only on the score of the whole unit layer, which lets a new survivor in while the rest of the code scores high
- Fail only on survivors, which lets in new code that no unit test reaches

## Consequences

- A gateseal pull request fails `mutation-diff` until each survivor in its changed lines is killed or marked, and until those lines score at least 90%.
- Code that only the integration layer runs needs a unit test that calls it in the test process, a marker, or a `suppress` entry.
- Each pull request runs the diff job. The run over the whole unit layer runs every week and when started by hand.
