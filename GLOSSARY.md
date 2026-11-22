# Glossary

## Policy

<a id="policy"></a>A **Policy** is a reviewed release of checks, versions, rules, and settings.

<a id="capability"></a>A **Capability** is a named group of checks, such as `hygiene`, that a repository selects.

<a id="policy-choice"></a>A **Policy Choice** is a value that a policy lets a repository select.

<a id="optional-hook"></a>An **Optional Hook** is a policy hook that a repository may skip.

<a id="reviewed-version"></a>A **Reviewed Version** is a tool version that the policy author reviewed.

<a id="version-exception"></a>A **Version Exception** selects another reviewed version with a reason and review date.

<a id="policy-lock"></a>A **Policy Lock** is `policy.lock`, the policy file that records reviewed upstream commits and checksums.

<a id="policy-author"></a>A **Policy Author** maintains the policy and tags its releases.

## Repository

<a id="manifest"></a>The **Manifest** is `gateseal.toml`, the repository's selection of policy and checks.

<a id="lock"></a>The **Lock** is `gateseal.lock`, the record of resolved inputs and generated files.

<a id="staged-lock"></a>The **Staged Lock** is the Lock in the Git index for a commit.

<a id="adoption"></a>**Adoption** selects a Git policy release that the Lock does not yet record. gateseal checks its tag at adoption.

<a id="repository-maintainer"></a>A **Repository Maintainer** manages the selection in one repository.

<a id="repository-hook"></a>A **Repository Hook** runs a repository script after policy hooks at commit, in `gateseal run`, and in CI. Its `stages` can move it from commit to `git push`, or to CI alone.

<a id="personal-hook"></a>A **Personal Hook** runs for one developer during an ordinary Git commit.

## Tools and machines

<a id="tool-kind"></a>A **Tool Kind** describes how gateseal acquires and runs a class of tools.

<a id="tool-definition"></a>A **Tool Definition** describes one tool and its hooks as data.

<a id="built-in-tool"></a>A **Built-in Tool** has a definition in the gateseal package.

<a id="team-defined-tool"></a>A **Team-defined Tool** has a definition in a team's policy.

<a id="project-tool"></a>A **Project Tool** reads the repository's own uv or Node project, so it finds the packages that the project installs.

<a id="policy-node"></a>The **Policy Node** is the Node version that a policy pins in `node`. Every tool from npm runs on it, whatever Node the machine has.
<a id="report"></a>A **Report** is a file that one hook writes and a later hook reads, such as coverage data. Git ignores it.

<a id="compiler-runtime"></a>The **Compiler Runtime** is the pre-commit version a gateseal release names and the Python that setup installs for it.

<a id="setup"></a>**Setup** installs that pre-commit and configures new clones to use its commit launcher.

<a id="launcher"></a>A **Launcher** is an executable hook of pre-commit in a clone. The commit launcher runs at `git commit`. The push launcher runs at `git push`, in a clone whose selection has a hook at a push.

<a id="ci-gate"></a>The **CI Gate** runs selected CI-stage checks and any history scans for an event.

<a id="history-scan"></a>A **History Scan** checks commits in a revision range, including content removed by a later commit.

<a id="time-budget"></a>A **Time Budget** is the time in whole seconds that a policy allows one CI Gate run. A slower run warns but keeps the result of its checks.
