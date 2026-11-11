# Share sealed hook environments through the interpreter store

Accepted on 2026-11-05 and implemented. It narrows [design record 0010](0010-share-one-sealed-interpreter-store-across-repositories.md)'s statement that pre-commit's hook environments stay in each attempt.

Each activation that prepared a new attempt gave it a new pre-commit home, so pre-commit cloned every hook repository and built its environment again. On Linux that was 40 MiB per attempt and 20.9 seconds of a 26.2-second warm activation, the largest cost left once the interpreter moved into the store.

pre-commit keeps a commit's unstaged-edit patches in its home, and cleanup reads them from the attempt it deletes. So each attempt still gets a pre-commit home of its own, and only the hook environments move. Activation builds each hook repository in an interpreter store entry of its own, by running `pre-commit install-hooks` with a configuration of that one repository and the entry as `PRE_COMMIT_HOME`. It then writes the attempt home's pre-commit database so that each repository's row names its clone in the store. pre-commit 4.7.0 finds every environment installed there and writes nothing outside the attempt.

An entry is named by the SHA-256 of its recipe: the platform, the Compiler Runtime's pre-commit closure, the interpreter the environment links to, the repository, its revision, and the configuration keys of each selected hook that can change its environment. The entry lives beside the interpreters under the same store lock, links, seal and collection, and gets read-only modes. Each attempt's record lists the entry among its runtime trees, so commits compare its metadata and `doctor` compares its content, as they do for the interpreter.

A rebuilt entry cannot match its old digests. pre-commit names each clone at random, and the environment records the attempt that built it. So the repair that one repository's activation makes stops other repositories' commits with `Run gateseal activate`, and their activations then reuse the repaired entry. Only damage causes a repair.

## Consequences

- A warm activation links the attempt to existing entries instead of building them, and a new worktree adds no hook environment to disk.
- Every commit compares the metadata of the hook entries it runs, which took 75 ms for the reference policy's two entries on Linux.
- A cold build holds the store lock for its clone and install, so another repository's activation waits and then reuses the entry. Git sets no network timeout by default, so a clone that hangs keeps it waiting until it is stopped.
- pre-commit rebuilds an environment whose health check fails, and it adds write permission before it deletes. If an interpreter entry disappears after a commit's checks pass, that commit's pre-commit can rebuild a shared environment, and other repositories' commits stop until an activation repairs it.

## Considered options

- Share one `PRE_COMMIT_HOME`. pre-commit would write every repository's patches into it, and cleanup could no longer tell which attempt a patch belongs to.
- Clone each hook repository with gateseal at a fixed path, so that a rebuild keeps its paths. The environment still records the attempt that built it, so the digests would change anyway.
- Keep only the marker and executable checks at commit time. Commits would save 75 ms, but an entry that other repositories' activations and pre-commit runs can reach would get weaker checks than the interpreter beside it.
- Keep hook entries in a store directory of their own. Every activation needs both kinds, so a second lock would not let activations overlap, and the store would need a second location and variable.
- Build environments with pre-commit-uv. Cold builds were about 6 seconds faster, but it added 22 MiB to every attempt's pre-commit environment and replaces pre-commit's installer through its internals, while a cold build now happens once per recipe per machine.
