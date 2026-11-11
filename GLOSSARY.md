# Glossary

This glossary defines the terms that the gateseal documentation uses for the
policy compiler and its reviewed policy releases.

## Terms

### The product

**gateseal**:
The policy compiler for reviewed repository checks. Write its name all lowercase, in prose and at the start of a sentence too, like pytest and uv. Only identifiers keep the case their language requires, such as `GatesealError` and `GATESEAL_PYTHON`.

### Policies

<a id="policy"></a>**Policy**:
A single, flat, versioned declarative release of reviewed check-tool pins, rules, exposed choices, and compatibility constraints. Each repository adopts one immutable release and upgrades it explicitly.

<a id="capability"></a>**Capability**:
A named set of tools that a **Policy** defines and a **Manifest** selects, such as `hygiene`, so a repository says what to check and the policy says with which tools. It groups checks by what they apply to, not by what kind of check they are. `hygiene` holds checks for any file, a language's capability holds all of that language's checks, and a concern across all files, such as `secrets`, has its own.

<a id="policy-choice"></a>**Policy Choice**:
A parameter that a Policy exposes for a repository to select, such as its formatter or SQL dialect.

<a id="reviewed-version"></a>**Reviewed Version**:
A version of one component that a **Policy** lists as reviewed with its rules. It is either the pin or an alternative that a **Version Exception** may select.

<a id="version-exception"></a>**Version Exception**:
A repository's explicit, validated move of one named component from its policy pin to another of its **Reviewed Versions**, with a reason and a review-by date.

<a id="policy-lock"></a>**Policy Lock**:
The generated file of a **Policy** that records every derived fact of its **Reviewed Versions**, such as revisions, archive URLs, and digests.

<a id="policy-author"></a>**Policy Author**:
The person who maintains a team's **Policy** repository, reviews its tool versions and rules, and tags its releases.

### Repositories

<a id="manifest"></a>**Manifest**:
The repository-owned definition of one selected Policy release, its quality capabilities, its Policy Choices, and its Version Exceptions.

<a id="lock"></a>**Lock**:
The committed record of the exact resolved gateseal inputs, such as the compiler pin and the policy release, and the ownership ledger for the generated checks configuration.

<a id="adoption"></a>**Adoption**:
Selecting a Git **Policy** release that the **Lock** does not record yet. It is the only time gateseal checks the release tag against its commit.

<a id="staged-lock"></a>**Staged Lock**:
The **Lock** in the index that a commit records. It alone selects the compiler that checks the commit.

<a id="repository-maintainer"></a>**Repository Maintainer**:
The person who edits one repository's **Manifest**.

### Tools

<a id="tool-kind"></a>**Tool Kind**:
A category of check tool that the compiler implements, such as a pre-commit hook repository or a release binary. It owns how its tools are acquired, verified, isolated, run, and version-checked.

<a id="tool-definition"></a>**Tool Definition**:
Declarative data that describes one tool as an instance of a **Tool Kind**.

<a id="built-in-tool"></a>**Built-in Tool**:
A **Tool Definition** shipped in the compiler package.

<a id="team-defined-tool"></a>**Team-defined Tool**:
A **Tool Definition** that a **Policy** ships in its `tools/` folder, of a **Tool Kind** the compiler implements. It carries its kind's guarantees, but a tool-specific guarantee only as far as its arguments declare it.

### Runtime and preparation

<a id="compiler-runtime"></a>**Compiler Runtime**:
The CPython and pre-commit closure that one compiler release carries, and that run its compiler, its hook gate, and pre-commit. Moving to a newer pre-commit means moving to a newer compiler release.

<a id="hook-python"></a>**Hook Python**:
The CPython release that a **Policy** pins for the hooks of its pre-commit hook repositories. A tool that parses code with the interpreter it runs on, such as Black, then knows the syntax of a newer Python than the **Compiler Runtime**'s. Without one, those hooks run on the **Compiler Runtime**'s CPython. It is not the project's Python, because gateseal never reads the repository's interpreter for it.

<a id="interpreter-store"></a>**Interpreter Store**:
The machine-wide directory of one read-only, sealed copy of each **Compiler Runtime**'s CPython, each **Hook Python**, and each hook environment built on them. Every **Prepared Attempt** on the machine runs from it, and only activation installs, repairs, or removes an entry.

<a id="prepared-attempt"></a>**Prepared Attempt**:
One activation's complete local preparation of a **Lock**. It is **published** while it is its worktree's current attempt. A commit, `gateseal run`, or `gateseal gate` **pins** the attempt it resolved, and cleanup keeps a pinned attempt until that commit, run, or gate ends.

<a id="candidate-compiler"></a>**Candidate Compiler**:
A compiler release that an upgrade's compiler target names. The current compiler installs it by its SHA-256 in a scratch environment, and has it configure and check the Manifest in a scratch repository before the Lock pins it.

### CI

<a id="ci-gate"></a>**CI Gate**:
The check a CI job runs after it prepares the Lock. It runs every hook at the CI stage on the files an event changed, and each History Scan over the commits the event added.

<a id="history-scan"></a>**History Scan**:
A tool's scan of every commit in a revision range, which the **CI Gate** runs so that a secret a later commit removed still fails it.

## How the terms fit together

- A **Manifest** selects exactly one **Policy** release, quality capabilities, **Policy Choices**, and **Version Exceptions**.
- A **Lock** records the immutable inputs selected for a **Manifest** and the ownership of the generated checks configuration.
- A **Policy** may be derived from another at authoring time. Consumer repositories still adopt one flat release.
- A Git **Policy** release is identified by its repository, directory, commit, and content digest. Its tag is a label that gateseal checks only at **Adoption**.
- A **Version Exception** selects one **Reviewed Version**, and the selected versions together must meet each other's requirements.
- Each worktree publishes at most one **Prepared Attempt** at a time, and no two worktrees share one.
- Each commit runs every stage from the one **Prepared Attempt** it pinned.
- A commit runs only when its **Staged Lock** is the Lock of the **Prepared Attempt** that its worktree publishes.
- Each compiler release carries exactly one **Compiler Runtime**, which no **Policy** or **Version Exception** changes, and a **Lock** records it.
- Every **Prepared Attempt** links itself to the **Interpreter Store** entries of its **Compiler Runtime**'s CPython, of its **Hook Python** if its policy pins one, and of its hook environments. An entry stays while a linked attempt exists.
- A **Capability** may offer alternatives as a **Policy Choice**, whose value names the one hook of its tools that runs.
- An upgrade's **Candidate Compiler** renders its compiler target, and must take the **Policy** and every **Version Exception** as they are.
- A **Policy Lock** belongs to one **Policy** release and is part of its content digest.
- Each **Built-in Tool** is a **Tool Definition** of one **Tool Kind**.
- A **Policy** defines its **Capabilities**, and each tool belongs to one. A **Team-defined Tool** never takes a **Built-in Tool**'s name.
- The **CI Gate** runs from a **Prepared Attempt** that its job activated, as a commit does, and selects its hooks by stage.
- A **Policy Author** releases a **Policy**, and a **Repository Maintainer** selects one release of it in a **Manifest**.
