"""Run the candidate compiler release that an upgrade's compiler target names.

Only a compiler renders its own Lock and outputs, so an upgrade to another
compiler asks that candidate. The current compiler installs the candidate's
verified wheel with uv into a scratch environment, which it removes
afterwards, and drives the candidate only through its public commands:
configure and check in a scratch repository that holds the planned Manifest,
and activate when the upgrade asks for it. The scratch repository has its own
Git directory inside the worktree's preparation directory, so the candidate
prepares and stores nothing for any worktree until that activation.
"""

import contextlib
import os
import shutil
import sys
import tempfile
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path

from gateseal import environments, sources
from gateseal.bootstrap import LOCK, MANIFEST, sha, tracked_bytes
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError, Note
from gateseal.managed_files import read, relative_name, write_atomic
from gateseal.manifest import BundledSource, parse_manifest
from gateseal.processes import execute, find_tool
from gateseal.validation import decode_json, mapping, sequence, string


def _environment() -> dict[str, str]:
    """Return the caller's environment without what points Git or Python elsewhere.

    The candidate reads uv's and mise's cache settings and the user's Git
    configuration from it, as the current compiler does.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if key not in sources.REDIRECTS
        and not key.startswith("PYTHON")
        and key not in ("VIRTUAL_ENV", "__PYVENV_LAUNCHER__")
    }


@dataclass(frozen=True)
class Candidate:
    """A candidate compiler installed in a scratch environment."""

    python: str
    artifact: CompilerArtifact

    @property
    def flags(self) -> tuple[str, ...]:
        """Return the configure flags that pin the candidate's own artifact."""
        locator = (
            "--compiler-wheel"
            if self.artifact.kind == "local-wheel"
            else "--compiler-source"
        )
        return (
            locator,
            self.artifact.location,
            "--compiler-sha256",
            self.artifact.sha256,
        )

    def run(self, repository: Path, *arguments: str) -> dict[str, object]:
        """Run one public command of the candidate and return its JSON result."""
        subject = f"gateseal {self.artifact.version} {arguments[0]}"
        done = execute(
            [self.python, "-I", "-B", "-m", "gateseal", "--repo", str(repository)]
            + ["--json", *arguments],
            repository,
            _environment(),
            allow_failure=True,
        )
        try:
            result = decode_json(done.stdout)
            code = string(result.get("code"), "code")
            diagnostics = [
                mapping(d, "diagnostic")
                for d in sequence(result.get("diagnostics"), "diagnostics")
            ]
        except GatesealError:
            detail = done.stderr.decode("utf-8", "replace").strip()
            raise GatesealError(
                "candidate_unavailable",
                f"{subject} did not return a JSON result: {detail}",
                4,
            ) from None
        if done.returncode:
            found = "; ".join(str(d.get("message")) for d in diagnostics[:1])
            steps = sequence(result.get("next_steps", []), "next_steps")
            step = steps[0] if steps else ""
            raise GatesealError(
                "candidate_rejected",
                f"{subject} stopped with {code}: {found}",
                done.returncode,
                str(step),
            )
        return result

    @contextlib.contextmanager
    def scratch(self, git: str, preparation: Path) -> Generator[Path]:
        """Yield a new empty repository for the candidate, deleted afterwards.

        It lives in the worktree's preparation directory, as sources.remote's
        scratch repositories do, so the user's includeIf gitdir: configuration
        applies to the candidate's policy fetch as it would to the worktree's.
        Activation's cleanup deletes one that an interrupted upgrade left.
        """
        directory = preparation / f".gateseal-{uuid.uuid4().hex}"
        try:
            directory.mkdir(mode=0o700)
            repository = directory / "repository"
            execute(
                [git, "init", "--quiet", str(repository)], directory, _environment()
            )
            yield repository
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def bundled(
        self, repository: Path, capabilities: tuple[str, ...]
    ) -> tuple[str, str]:
        """Return the release and digest of the policy the candidate bundles."""
        selected = [flag for name in capabilities for flag in ("--capability", name)]
        self.run(repository, "configure", *selected, "--policy", "bundled", *self.flags)
        source = parse_manifest(tracked_bytes(read(repository, MANIFEST))).source
        recorded = mapping(
            decode_json(tracked_bytes(read(repository, LOCK))).get("policy"),
            "lock.policy",
        )
        if not isinstance(source, BundledSource):
            raise GatesealError(
                "candidate_rejected", "The candidate selected no bundled policy."
            )
        return source.release, string(recorded.get("digest"), "lock.policy.digest")

    def configure(
        self, repository: Path, manifest: bytes
    ) -> tuple[dict[str, bytes], tuple[Note, ...]]:
        """Configure and check manifest with the candidate; return its tracked files.

        The result holds the Manifest, the candidate's Lock and every output that
        Lock records, each checked against it, and the notes configure gave.
        """
        write_atomic(repository / MANIFEST, manifest, 0o644)
        result = self.run(repository, "configure", *self.flags)
        self.run(repository, "check")
        data = tracked_bytes(read(repository, LOCK))
        document = decode_json(data)
        files = {MANIFEST: manifest, LOCK: data}
        for name, entry in mapping(document.get("outputs"), "lock.outputs").items():
            if name in files or relative_name(name).split("/")[0] == ".git":
                raise self._disagrees(f"its Lock claims {name} as an output")
            files[name] = tracked_bytes(read(repository, name))
            if mapping(entry, name).get("sha256") != sha(files[name]):
                raise self._disagrees(f"{name} differs from its Lock record")
        if CompilerArtifact.parse(document.get("compiler")) != self.artifact:
            raise self._disagrees("its Lock pins another compiler")
        if document.get("manifest_sha256") != sha(manifest):
            raise self._disagrees("its Lock records another Manifest")
        code = result.get("code")
        notes = tuple(
            Note(str(d.get("severity")), str(d.get("category")), str(d.get("message")))
            for d in (
                mapping(item, "diagnostic")
                for item in sequence(result.get("diagnostics"), "diagnostics")
            )
            if d.get("category") != code
        )
        return files, notes

    def _disagrees(self, detail: str) -> GatesealError:
        return GatesealError(
            "candidate_rejected",
            f"gateseal {self.artifact.version} configured a scratch repository, but "
            f"{detail}.",
        )


@contextlib.contextmanager
def installed(artifact: CompilerArtifact, root: Path) -> Generator[Candidate]:
    """Install artifact with its SHA-256 into a scratch environment, removed afterwards.

    The environment runs the current command's Python, which every release of
    one CPython minor accepts; uv refuses a wheel whose Python it does not.
    """
    with tempfile.TemporaryDirectory(prefix="gateseal-candidate-") as directory:
        scratch = Path(directory)
        source = (
            Path(artifact.location).as_uri()
            if artifact.kind == "local-wheel"
            else artifact.location
        )
        requirements = scratch / "compiler.txt"
        requirements.write_text(
            f"gateseal @ {source} --hash=sha256:{artifact.sha256}\n"
        )
        try:
            python = environments.package_environment(
                find_tool("uv"),
                root,
                environments.uv_environment(),
                sys.executable,
                scratch / "compiler",
                requirements,
            )
        except GatesealError as exc:
            raise GatesealError(
                "candidate_unavailable",
                f"uv could not install gateseal {artifact.version} from "
                f"{artifact.location}: {exc}",
                4,
                "Check the candidate wheel's location and SHA-256, then retry.",
            ) from exc
        yield Candidate(python, artifact)
