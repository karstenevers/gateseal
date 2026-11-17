import dataclasses
import json

import pytest
from conftest import TRACKED, preserved
from gateseal import __version__
from gateseal.compiler_release import PRE_COMMIT, REPOSITORY
from gateseal.diagnostics import GatesealError
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from installation import (
    install,
    next_release,
    package_version,
    process,
    success,
    tag_release,
    versioned_package,
)

PRE_COMMIT_EXCEPTION = (
    '\n[exceptions.pre-commit]\nversion = "4.5.0"\nreason = "Upstream bug"\n'
    "review_by = 2026-12-31\n"
)
REMOVE_EXCEPTION = (
    "Remove [exceptions.pre-commit] from gateseal.toml, then run gateseal "
    "configure. A newer pre-commit comes with a newer gateseal release."
)


def test_hooks_need_the_pre_commit_that_renders_them(monkeypatch):
    policy = load_bundled_policy()
    source = BundledSource(policy.release)
    resolve(parse_manifest(render(("hygiene", "python", "secrets"), source)), policy)
    monkeypatch.setattr("gateseal.manifest.PRE_COMMIT", "4.3.0")
    resolve(parse_manifest(render(("hygiene", "python"), source)), policy)
    with pytest.raises(GatesealError) as failure:
        resolve(parse_manifest(render(("hygiene", "secrets"), source)), policy)
    assert failure.value.code == "incompatible_runtime"
    assert str(failure.value) == (
        "The betterleaks hook needs pre-commit 4.4.0 or newer, but gateseal "
        f"{__version__} runs pre-commit 4.3.0."
    )
    assert failure.value.next_step == (
        "Use a gateseal release whose pre-commit is 4.4.0 or newer."
    )


def test_pre_commit_exception_names_the_compiler_runtime():
    policy = load_bundled_policy()
    text = render(("hygiene",), BundledSource(policy.release)).decode()
    manifest = parse_manifest((text + PRE_COMMIT_EXCEPTION).encode())
    with pytest.raises(GatesealError) as failure:
        resolve(manifest, policy)
    assert failure.value.code == "runtime_exception"
    assert str(failure.value) == (
        "exceptions.pre-commit selects a pre-commit version, but each gateseal "
        f"release carries its own: gateseal {__version__} runs pre-commit 4.7.0."
    )
    assert failure.value.next_step == REMOVE_EXCEPTION


def tracked(root):
    return {name: (root / name).read_bytes() for name in TRACKED}


@pytest.mark.integration
def test_pre_commit_exception_stops_configure_and_check(installed, prepared):
    manifest = prepared / "gateseal.toml"
    before = tracked(prepared)
    with preserved(manifest) as original:
        manifest.write_bytes(original + PRE_COMMIT_EXCEPTION.encode())
        result = installed.cli(prepared, "configure", code=2)
        assert result["code"] == "runtime_exception"
        assert result["next_steps"] == [REMOVE_EXCEPTION]
        assert tracked(prepared) | {manifest.name: original} == before
        refused = installed.cli(prepared, "check", code=2)
        assert refused["code"] == "runtime_exception"
    assert success(["git", "status", "--porcelain"], prepared).stdout == ""


def runtime_release(installed, directory, version, pre_commit):
    """Install a release at version that runs pre_commit."""
    package = versioned_package(directory / "package", version)
    path = package / "src" / "gateseal" / "compiler_release.py"
    pinned = f'PRE_COMMIT = "{PRE_COMMIT}"\n'
    assert pinned in path.read_text()
    path.write_text(path.read_text().replace(pinned, f'PRE_COMMIT = "{pre_commit}"\n'))
    return package, dataclasses.replace(
        install(directory, package), environment=installed.environment
    )


@pytest.fixture(scope="module")
def old_runtime(installed, tmp_path_factory):
    directory = tmp_path_factory.mktemp("old runtime ü").resolve()
    return runtime_release(installed, directory, package_version(), "4.3.0")[1]


@pytest.fixture(scope="module")
def next_runtime(installed, gateseal_origin, tmp_path_factory):
    """Install and tag a release three after this one, whose pre-commit is newer.

    next_installed and the variant release in test_gateseal_upgrade hold the
    two before it, and each version's tag names one commit.
    """
    directory = tmp_path_factory.mktemp("next runtime ü").resolve()
    version = next_release(next_release(next_release(package_version())))
    package, installation = runtime_release(installed, directory, version, "4.8.0")
    tag_release(gateseal_origin, version, package)
    return installation


@pytest.mark.integration
def test_move_to_a_release_with_another_pre_commit(installed, next_runtime, configured):
    """Follow the README's move to another release, whose pre-commit differs."""
    config = configured / ".pre-commit-config.yaml"
    assert installed.cli(configured, "check")["code"] == "consistent"
    moved = next_runtime.cli(configured, "upgrade")
    assert moved["code"] == "upgraded"
    assert any("pre-commit 4.8.0" in step for step in moved["next_steps"])
    assert '"minimum_pre_commit_version": "4.8.0"' in config.read_text()
    assert next_runtime.cli(configured, "check")["code"] == "consistent"


@pytest.mark.integration
def test_compiler_refuses_hooks_its_pre_commit_cannot_run(old_runtime, consumer):
    secrets = ["--capability", "hygiene", "--capability", "secrets"]
    refusal = old_runtime.cli(consumer, "configure", *secrets, code=2)
    assert refusal["code"] == "incompatible_runtime"
    message = refusal["diagnostics"][0]["message"]
    assert "betterleaks hook needs pre-commit 4.4.0" in message
    assert "runs pre-commit 4.3.0" in message
    assert not (consumer / "gateseal.toml").exists()
    configured = old_runtime.cli(consumer, "configure", "--capability", "hygiene")
    assert configured["code"] == "configured"
    assert "with pre-commit 4.3.0." in json.dumps(configured["diagnostics"])
    config = (consumer / ".pre-commit-config.yaml").read_text()
    assert '"minimum_pre_commit_version": "4.3.0"' in config


@pytest.mark.integration
def test_status_and_doctor_name_the_locked_gateseal(
    installed, gateseal_origin, prepared
):
    tag = f"v{__version__}^{{commit}}"
    commit = success(["git", "rev-parse", tag], gateseal_origin).stdout.strip()
    note = (
        f"gateseal {__version__} from {REPOSITORY} at commit {commit}, with "
        "pre-commit 4.7.0."
    )
    status = installed.cli(prepared, "status")
    notes = [d for d in status["diagnostics"] if d["category"] == "gateseal"]
    assert notes == [{"severity": "info", "category": "gateseal", "message": note}]
    doctor = process(
        [installed.command, "--repo", prepared, "doctor"],
        prepared,
        installed.environment,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert note in doctor.stdout.splitlines()
