"""Acquire Git policy releases by exact commit, and keep them per worktree.

Only configure, activate, check and upgrade acquire a release, and they fetch
it only when the worktree has not stored it. Every other command, and every
commit, reads a release that an earlier acquisition stored, and checks its
digest.
"""

import contextlib
import hashlib
import os
import shutil
import signal
import subprocess
import uuid
from collections.abc import Generator
from pathlib import Path

from gateseal import bootstrap
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import open_directory, read, write_atomic
from gateseal.manifest import GitSource
from gateseal.policy import Policy, content_stream, load_policy, load_policy_stream

STORE = "policies"
"""The directory of stored releases, inside a worktree's preparation directory."""

TIMEOUT = 120  # pytestmut: skip[num.shift] -- any timeout near two minutes serves
"""Seconds one Git command may take before it is stopped."""

# Variables that would point Git at another repository, index, object store or
# configuration file than the scratch repository's, rewrite the objects it
# reads, or trace a transfer with its authentication headers.
REDIRECTS = frozenset(
    {
        "GIT_CONFIG",
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_NAMESPACE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_REPLACE_REF_BASE",
        "GIT_GRAFT_FILE",
        "GIT_SHALLOW_FILE",
        "GIT_QUARANTINE_PATH",
        "GIT_CURL_VERBOSE",
    }
)

OPTIONS = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "transfer.fsckObjects=true",
    "-c",
    "protocol.version=2",
    "-c",
    "gc.auto=0",
    "-c",
    "maintenance.auto=false",
    "-c",
    "http.lowSpeedLimit=1000",
    "-c",
    "http.lowSpeedTime=30",
)


def _environment() -> dict[str, str]:
    """Return the caller's Git transport settings, made noninteractive.

    Global and system configuration stay, as for git clone, so credential
    helpers, url.<base>.insteadOf and SSH settings decide how Git connects.
    """
    env = {
        key: value
        for key, value in bootstrap.clean_environment().items()
        if key not in REDIRECTS and not key.startswith("GIT_TRACE")
    }
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GCM_INTERACTIVE": "never",
            "SSH_ASKPASS_REQUIRE": "never",
        }
    )
    return env


def _stop(process: subprocess.Popen[bytes]) -> None:
    """Kill git's process group, which a terminal's Ctrl-C does not reach."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.communicate()


class Remote:
    """A scratch bare repository whose origin is one policy repository.

    It lives in the worktree's preparation directory, so the user's includeIf
    gitdir: and hasconfig:remote.*.url: configuration applies as it would to a
    clone there. The consumer repository's own configuration, which can carry
    another credential, does not apply. subject names the repository in
    failures, such as the policy repository or a tool's.
    """

    def __init__(
        self,
        git: str,
        directory: Path,
        repository: str,
        subject: str = "the policy repository",
    ) -> None:
        self.git = git
        self.directory = directory
        self.repository = repository
        self.subject = subject

    def run(
        self, *arguments: str, cwd: Path | None = None, reading: str = ""
    ) -> subprocess.CompletedProcess[bytes]:
        """Run git without a terminal, so no prompt can wait for input.

        reading names what the command reads, such as a tag, for its failure.
        """
        command = [self.git, *OPTIONS, *arguments]
        if cwd is None:
            command[1:1] = [f"--git-dir={self.directory}"]
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd or self.directory,
                env=_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise GatesealError("tool_unavailable", "Git could not start.", 4) from exc
        try:
            stdout, stderr = process.communicate(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            _stop(process)
            raise self.failed(
                f"Git did not finish within {TIMEOUT} seconds.", reading
            ) from None
        except BaseException:
            _stop(process)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def failed(self, detail: str, reading: str = "") -> GatesealError:
        subject = f"{reading} of {self.subject}" if reading else self.subject
        return GatesealError(
            "policy_fetch_failed",
            f"Git could not read {subject} {self.repository}: {detail}",
            4,
            f"Check that git ls-remote {self.repository} works in this "
            "repository with your Git credentials. If it does not, ask the "
            "policy repository's owners for read access.",
        )

    def checked(self, *arguments: str, reading: str = "") -> bytes:
        result = self.run(*arguments, reading=reading)
        if result.returncode:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise self.failed(detail, reading)
        return result.stdout

    def peeled(self, tag: str) -> str | None:
        """Return the commit origin's tag names, or None without the tag.

        ls-remote matches patterns against ref tails, so only the exact tag and
        its peeled entry count. An annotated tag's peeled entry names its
        commit.
        """
        name = f"refs/tags/{tag}"
        found: dict[str, str] = {}
        listing = self.checked(
            "ls-remote", "origin", name, name + "^{}", reading=f"tag {tag}"
        )
        for line in listing.decode("utf-8", "replace").splitlines():
            oid, _, ref = line.partition("\t")
            found[ref] = oid
        return found.get(name + "^{}", found.get(name))

    def tagged(self, tag: str) -> str:
        """Return the commit the policy repository's tag names."""
        target = self.peeled(tag)
        if target is None:
            raise GatesealError(
                "policy_tag_missing",
                f"The policy repository has no tag {tag}.",
                next_step="Select a release tag of the policy repository in "
                "gateseal.toml, then run gateseal configure.",
            )
        return target

    def verify_tag(self, source: GitSource) -> str:
        """Require source's tag to name its commit, and say so.

        The tag being in the policy repository also shows the commit is part of
        it, not an object the host serves from another repository.
        """
        tag, commit = source.tag, source.commit
        target = self.tagged(tag)
        if target != commit:
            raise GatesealError(
                "policy_tag_mismatch",
                f"Tag {tag} names commit {target}, not the selected commit {commit}.",
                next_step=f"Review the release, then set commit = {target!r} in "
                "gateseal.toml, or select the tag that names the selected "
                "commit, and run gateseal configure.",
            )
        return f"Tag {tag} names commit {commit}, as adoption requires."

    def fetch(self, source: GitSource) -> Policy:
        """Fetch source's commit alone and load the policy in its tree.

        Every file the policy names must be a regular, non-executable file in
        that tree. Blobs are read with cat-file, so no filter or line-ending
        conversion applies, and the bytes are the commit's on every machine.
        """
        commit = source.commit
        reading = f"commit {commit}"
        self.checked(
            "fetch",
            "--quiet",
            "--no-tags",
            "--no-write-fetch-head",
            "--no-recurse-submodules",
            "--depth=1",
            "origin",
            commit,
            reading=reading,
        )
        if self.checked("cat-file", "-t", commit, reading=reading).strip() != b"commit":
            raise self.failed(f"{commit} is not a commit.", reading)
        entries = self._tree(source)

        def read_asset(name: str) -> bytes:
            mode, kind, oid = entries.get(name, ("", "", ""))
            if not mode:
                raise FileNotFoundError(name)
            if mode != "100644" or kind != "blob":
                described = {"100755": "an executable", "120000": "a symbolic link"}
                raise GatesealError(
                    "unsafe_policy_entry",
                    f"Policy file {name} is {described.get(mode, 'not a file')}; "
                    "policy data holds only regular, non-executable files.",
                )
            return self.checked("cat-file", "blob", oid, reading=reading)

        return load_policy(read_asset)

    def _tree(self, source: GitSource) -> dict[str, tuple[str, str, str]]:
        """Return every entry of source's tree by name: mode, type, object."""
        revision = (
            f"{source.commit}^{{tree}}"
            if source.path == "."
            else f"{source.commit}:{source.path}"
        )
        tree = self.run("rev-parse", "--verify", "--quiet", revision)
        oid = tree.stdout.decode().strip()
        kind = self.run("cat-file", "-t", oid).stdout.strip() if oid else b""
        if tree.returncode or kind != b"tree":
            raise GatesealError(
                "policy_path_missing",
                f"Commit {source.commit} has no directory {source.path}.",
                next_step="Name the policy directory in path in gateseal.toml, "
                "then run gateseal configure.",
            )
        entries: dict[str, tuple[str, str, str]] = {}
        listing = self.checked(
            "ls-tree", "-r", "-z", oid, reading=f"commit {source.commit}"
        )
        for item in filter(None, listing.split(b"\0")):
            header, _, name = item.partition(b"\t")
            mode, kind_name, entry = header.decode().split(" ")
            entries[name.decode("utf-8", "replace")] = (mode, kind_name, entry)
        return entries


@contextlib.contextmanager
def remote(
    git: str,
    preparation: Path,
    repository: str,
    subject: str = "the policy repository",
) -> Generator[Remote]:
    """Yield a scratch Remote for repository, and delete it afterwards.

    Activation's cleanup deletes a scratch repository an interrupted run left,
    since its name starts with .gateseal-.
    """
    directory = preparation / f".gateseal-{uuid.uuid4().hex}.git"
    origin = Remote(git, directory, repository, subject)
    try:
        created = origin.run(
            "init",
            "--quiet",
            "--bare",
            "--template=",
            "--object-format=sha1",
            str(directory),
            cwd=preparation,
        )
        if created.returncode:
            raise origin.failed(created.stderr.decode("utf-8", "replace").strip())
        origin.checked("config", "remote.origin.url", repository)
        yield origin
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _name(source: GitSource, digest: str) -> str:
    """Name a stored release by its commit, its directory and its digest."""
    identity = hashlib.sha256(f"{source.commit}\0{source.path}".encode()).hexdigest()
    return f"{identity}-{digest.removeprefix('sha256:')}"


def stored(preparation: Path, source: GitSource, digest: str) -> Policy | None:
    """Return the stored release of source with digest, or None.

    A stored release whose content no longer has its digest is damaged
    preparation, which activation repairs by fetching it again.
    """
    name = _name(source, digest)
    try:
        data = read(preparation / STORE, name, non_executable=True)
    except FileNotFoundError:
        return None
    try:
        policy = load_policy_stream(data)
    except GatesealError as exc:
        raise _damaged(name, str(exc)) from exc
    if policy.content_digest != digest:
        raise _damaged(name, f"Its content has digest {policy.content_digest}.")
    return policy


def stored_any(preparation: Path, source: GitSource) -> Policy | None:
    """Return a release stored for source's commit and directory, or None."""
    prefix = _name(source, "")
    with contextlib.suppress(FileNotFoundError):
        for entry in sorted(os.scandir(preparation / STORE), key=lambda e: e.name):
            if entry.name.startswith(prefix):
                digest = "sha256:" + entry.name.removeprefix(prefix)
                return stored(preparation, source, digest)
    return None


def _damaged(name: str, detail: str) -> GatesealError:
    return GatesealError(
        "policy_content_mismatch",
        f"The stored policy release {STORE}/{name} changed. {detail}",
        3,
        "gateseal activate",
    )


def store(preparation: Path, source: GitSource, policy: Policy) -> None:
    """Store policy as source's release, replacing a damaged copy.

    Each release is one file, its content stream, written atomically. Only
    configure, activate, check and upgrade store, under the worktree's
    operation lock, so a scratch file that is already there was left by an
    interrupted run.
    """
    directory = preparation / STORE
    directory.mkdir(mode=0o700, exist_ok=True)
    with open_directory(directory) as fd:
        for name in os.listdir(fd):
            if name.startswith(".gateseal-"):
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(name, dir_fd=fd)
    write_atomic(
        directory / _name(source, policy.content_digest),
        content_stream(policy.assets),
        0o644,
    )
