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

<a id="optional-hook"></a>**Optional Hook**:
A hook that a **Policy** marks as one that a repository may leave out. A repository lists those it leaves out in the `skip` **Policy Choice** of the hook's **Capability**. A commit and the **CI Gate** then both skip them.

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
The repository-owned definition of one selected Policy release, its quality capabilities, its Policy Choices, its Version Exceptions, and its **Repository Hooks**.

<a id="lock"></a>**Lock**:
The committed record of the exact resolved gateseal inputs, such as the gateseal pin and the policy release, and the ownership ledger for the generated checks configuration.

<a id="adoption"></a>**Adoption**:
Selecting a Git **Policy** release that the **Lock** does not record yet. It is the only time gateseal checks the release tag against its commit.

<a id="staged-lock"></a>**Staged Lock**:
The **Lock** in the index that a commit records. It alone selects the compiler that checks the commit.

<a id="repository-maintainer"></a>**Repository Maintainer**:
The person who edits one repository's **Manifest**.

<a id="repository-hook"></a>**Repository Hook**:
A hook that a **Manifest**'s `[hooks]` table adds to run a script of the repository. It runs after every hook of the **Policy**, at a commit, in `gateseal run` and in the **CI Gate**. It only adds a check, and gateseal installs nothing for it.

<a id="personal-hook"></a>**Personal Hook**:
A hook that one developer adds in Git's configuration or in a clone's hooks folder. It touches no file that gateseal writes, and the **CI Gate** does not run it.

### Tools

<a id="tool-kind"></a>**Tool Kind**:
A category of check tool that the compiler implements, such as a pre-commit hook repository or a release binary. It owns how its tools are acquired, verified, isolated, and run.

<a id="tool-definition"></a>**Tool Definition**:
Declarative data that describes one tool as an instance of a **Tool Kind**.

<a id="built-in-tool"></a>**Built-in Tool**:
A **Tool Definition** shipped in the compiler package.

<a id="team-defined-tool"></a>**Team-defined Tool**:
A **Tool Definition** that a **Policy** ships in its `tools/` folder, of a **Tool Kind** the compiler implements. It carries its kind's guarantees, but a tool-specific guarantee only as far as its arguments declare it.

### Machines

<a id="compiler-runtime"></a>**Compiler Runtime**:
The pre-commit version that one gateseal release names, and the Python that `gateseal setup` installs it on. Python hooks run on that Python when a commit uses setup's pre-commit. Moving to a newer pre-commit means moving to a newer gateseal release.

<a id="setup"></a>**Setup**:
The step that a person runs once per machine, `gateseal setup`. It installs the **Compiler Runtime** in a folder that gateseal owns. It also makes Git copy the **Launcher** into every new clone. A developer can install pre-commit and run `pre-commit install` in each clone instead. That is enough for commits, but `gateseal run`, `status` and `doctor` need the **Compiler Runtime** that setup installs.

<a id="launcher"></a>**Launcher**:
pre-commit's commit hook, `.git/hooks/pre-commit` in a clone, which starts pre-commit at each `git commit`. In a repository without `.pre-commit-config.yaml`, the Launcher that **Setup** writes skips pre-commit and the commit goes on. One from plain `pre-commit install` stops the commit.

### CI

<a id="ci-gate"></a>**CI Gate**:
The check a CI job runs after it prepares the Lock. It runs every hook at the CI stage on the files an event changed, and each History Scan over the commits the event added.

<a id="history-scan"></a>**History Scan**:
A tool's scan of every commit in a revision range, which the **CI Gate** runs so that a secret a later commit removed still fails it.

## How the terms fit together

- A **Manifest** selects exactly one **Policy** release, quality capabilities, **Policy Choices**, and **Version Exceptions**.
- A **Lock** records the immutable inputs selected for a **Manifest** and the ownership of the generated checks configuration.
- Every gateseal release reads a **Lock**'s ownership ledger: its format, its gateseal pin, the **Manifest**'s SHA-256, its **Policy**'s content digest, and its outputs. The release that wrote the **Lock** owns its other fields.
- A **Policy** may be derived from another at authoring time. Consumer repositories still adopt one flat release.
- A Git **Policy** release is identified by its repository, directory, commit, and content digest. Its tag is a label that gateseal checks only at **Adoption**.
- A **Version Exception** selects one **Reviewed Version**, and the selected versions together must meet each other's requirements.
- Each gateseal release names exactly one **Compiler Runtime**, which no **Policy** or **Version Exception** changes.
- **Setup** installs the **Compiler Runtime** and the **Launcher** on a machine. A commit needs only a **Launcher** and a pre-commit. It builds gateseal's own hook from the commit that the **Staged Lock** pins.
- A **Capability** may offer alternatives as a **Policy Choice**, whose value names the one hook of its tools that runs.
- A **Capability** may offer **Optional Hooks** that a **Manifest** skips with a **Policy Choice**. A **Manifest** can skip no other hook of the **Policy**.
- A change that the **CI Gate** must run goes in the **Policy** or the **Manifest**, such as a **Repository Hook**, and the **Lock** records it. A change for one developer is a **Personal Hook** in Git.
- A **Policy Lock** belongs to one **Policy** release and is part of its content digest.
- Each **Built-in Tool** is a **Tool Definition** of one **Tool Kind**.
- A **Policy** defines its **Capabilities**, and each tool belongs to one. A **Team-defined Tool** never takes a **Built-in Tool**'s name.
- The **CI Gate** runs the pre-commit that `gateseal setup` installed, as a commit after **Setup** does, and selects its hooks by stage.
- A **Policy Author** releases a **Policy**, and a **Repository Maintainer** selects one release of it in a **Manifest**.
