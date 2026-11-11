# Share one sealed interpreter store across repositories

Accepted on 2026-11-02 and implemented. It narrows [design record 0003](0003-prepare-gateseal-tools-before-commits.md)'s rule that commits start the exact prepared interpreter: the interpreter is still exact and verified, but a machine holds one copy of it instead of one per attempt. [Design record 0011](0011-share-sealed-hook-environments-through-the-interpreter-store.md) moves pre-commit's hook environments into the same store.

Each activation used to unpack the Compiler Runtime's CPython into its new attempt. A worktree kept up to two copies of 69 to 108 MB, and every activation gave the interpreter a new path. macOS app firewalls such as Little Snitch match an ad-hoc-signed interpreter by path, so a rule that allowed it forever stopped matching after the next activation.

So every attempt now runs the interpreter from one machine-wide store, in an entry named by the SHA-256 of the Python archive. The entry stays at the path where uv installs it, because uv writes that absolute path into the interpreter. Activation installs a missing or damaged entry under a store lock that every repository's activation takes, and writes the entry's seal of metadata and content digests last, so an entry without a seal is partial. It gives every file a fixed modification time and removes write permission, so a reinstall at the same path reproduces the sealed digests and repairs the entry for every repository that recorded them. Each attempt links itself into its entry before it uses it, and activation removes an entry once no linked attempt directory exists. Commits read the entry through the attempt's record and never take the store lock.

pre-commit's hook environments stay in each attempt. pre-commit saves a commit's unstaged edits in its home, and cleanup reads those patches from the attempt it deletes, so a shared home would need its own ownership rules. Design record 0011 weighs that against what rebuilding them costs each activation, as it did before this change: 40 MiB and about 21 seconds of a 26-second activation on Linux.

## Considered options

- Keep one interpreter per attempt. The firewall rule and the disk cost stay as they were.
- Unpack into a temporary directory and rename it into the store. uv's absolute paths in the interpreter would then name the temporary directory.
- Count an entry's users in a file. A killed activation or a deleted clone leaves the count wrong, while a link to a missing attempt directory is visibly dead.
- Key entries by Python version. Two archives of one version would then share an entry, and the seal could not say which archive it holds.
