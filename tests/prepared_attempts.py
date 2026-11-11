"""Stand-in attempts that the real runtime accepts as prepared.

uv builds a real attempt's environments from downloads, so prepare writes an
attempt whose interpreter, compiler, pre-commit, hook executables and
Betterleaks are shell scripts, and the record seals them as activation seals
its environments.
"""

import hashlib
import uuid
from pathlib import Path

from gateseal import __version__, bootstrap, interpreter_store, lock, runtime
from gateseal.validation import canonical

SOURCE = (
    "https://raw.githubusercontent.com/example/wheels/"
    f"{'0' * 40}/artifacts/gateseal-{__version__}-py3-none-any.whl"
)
PROBES = ("trailing-whitespace-fixer", "end-of-file-fixer", "check-yaml")
PRE_COMMIT = "pre-commit 4.7.0"
PRE_COMMIT_SCRIPT = f"#!/bin/sh\necho {PRE_COMMIT}\n"
BETTERLEAKS = '#!/bin/sh\n[ "$1" = version ] && echo {version}\nexit 0\n'
BETTERLEAKS_SCRIPT = BETTERLEAKS.format(version="1.9.0")


def configure(worktree, capabilities):
    """Write the tracked policy for capabilities, with a compiler not yet fetched."""
    lock.configure(
        worktree.root,
        worktree.git,
        worktree.preparation,
        list(capabilities),
        lock.PolicyRequest(),
        None,
        SOURCE,
        "a" * 64,
        None,
    )


def executable(path, text="#!/bin/sh\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


def answer(line):
    return f"#!/bin/sh\necho {line}\n"


def seal(tree, roots):
    return bootstrap.TreeDigests(
        bootstrap.tree_digest(tree, roots, content=False),
        bootstrap.tree_digest(tree, roots, content=True),
    )


def prepare(
    worktree,
    locked,
    *,
    linked=True,
    probe="#!/bin/sh\n",
    pre_commit=PRE_COMMIT_SCRIPT,
    betterleaks=BETTERLEAKS_SCRIPT,
    replaces=None,
    publish=True,
):
    """Write a complete stand-in attempt for locked and publish it.

    probe, pre_commit and betterleaks are the scripts of the hook executables,
    the pre-commit Python and the Betterleaks binary.
    """
    attempt = worktree.preparation / ("attempt-" + uuid.uuid4().hex)
    attempt.mkdir()
    owner = attempt if linked else attempt.with_name("attempt-" + "0" * 32)

    def install(entry, descriptor):
        executable(entry / "bin" / "python")

    name = hashlib.sha256(attempt.name.encode()).hexdigest()
    identity = {"key": "stand-in"}
    with interpreter_store.sealed(name, identity, owner, install) as found:
        entry, interpreter_seal = found
    compiler = executable(attempt / "compiler/bin/python", answer(__version__))
    pre_commit_python = executable(attempt / "pre-commit/bin/python", pre_commit)
    hooks = worktree.root.parent / "hook entries" / attempt.name
    environment = hooks / "env"
    executables = [executable(environment / "bin" / name, probe) for name in PROBES]
    hook_python = executable(environment / "bin" / "python")
    (environment / ".install_state_v1").write_text("{}")
    paths = bootstrap.PreparedPaths(
        git=Path(worktree.git),
        interpreter=entry / "bin" / "python",
        compiler_python=compiler,
        pre_commit_python=pre_commit_python,
        bootstrap=executable(attempt / "compiler/bootstrap.py"),
        pre_commit_home=attempt / "pre-commit-home",
        tools=attempt / "tools",
    )
    paths.tools.mkdir()
    (attempt / "hook").mkdir()
    (attempt / "hook" / bootstrap.PYTHON).symlink_to(paths.interpreter)
    (attempt / "hook" / bootstrap.BOOTSTRAP).symlink_to("../compiler/bootstrap.py")
    owned = [compiler.parents[1], pre_commit_python.parents[1], hooks]
    native = {}
    if any(tool.definition.name == "betterleaks" for tool in locked.selection.tools):
        binary = executable(attempt / "betterleaks/betterleaks", betterleaks)
        (paths.tools / "gateseal-betterleaks").symlink_to(binary)
        native["betterleaks"] = bootstrap.PreparedTool("1.9.0", binary)
        owned.append(binary.parent)
    roots = (entry, *owned)
    record = bootstrap.PreparedRecord(
        attempt=attempt,
        root=worktree.root,
        lock_sha256=bootstrap.sha(locked.snapshot.lock),
        platform=bootstrap.platform.system() + "-" + bootstrap.platform.machine(),
        paths=paths,
        outputs=tuple(locked.snapshot.outputs),
        native_tools=native,
        hook_python=None,
        trees={entry: interpreter_seal, **{tree: seal(tree, roots) for tree in owned}},
        hook_environments={
            environment: bootstrap.HookEnvironment(
                "pre-commit-hooks",
                hook_python,
                bootstrap.file_hash(environment / ".install_state_v1"),
                tuple(executables),
            )
        },
        replaces=replaces,
    )
    (attempt / bootstrap.RECORD).write_bytes(canonical(record.document()))
    if publish:
        worktree.hook.parent.mkdir(exist_ok=True)
        runtime._publish(worktree, locked, attempt)
    return attempt
