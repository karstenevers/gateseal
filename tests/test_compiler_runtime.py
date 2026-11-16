import dataclasses
import json
from importlib.resources import files

import pytest
from conftest import TRACKED, preserved
from gateseal import __version__
from gateseal.compiler_release import REPOSITORY, load_runtime, parse_runtime
from gateseal.diagnostics import GatesealError
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.platforms import TARGETS
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


def committed_runtime():
    return json.loads(files("gateseal").joinpath("data", "runtime.lock").read_bytes())


def refused(document):
    with pytest.raises(GatesealError) as failure:
        parse_runtime(json.dumps(document).encode())
    return failure.value


def test_committed_runtime_runs_every_target():
    runtime = load_runtime()
    assert runtime.pre_commit == "4.7.0"
    for target in TARGETS.values():
        [(key, record)] = runtime.interpreter(target).items()
        assert key == f"cpython-3.14.10-{target.python}"
        assert record["build"] == "20261026"
    lines = runtime.requirements().decode().splitlines()
    assert len(lines) == 11
    assert lines[7].startswith("pre-commit==4.7.0 --hash=sha256:")


@pytest.mark.parametrize(
    "version",
    [
        "26.3",
        "0",
        "1.2.3",
        "1!2.0",
        "1.0rc1",
        "1.0.post1",
        "1.0.dev1",
        "2!1.2a0.post0.dev0",
    ],
)
def test_runtime_accepts_a_canonical_package_version(version):
    document = committed_runtime()
    document["pre_commit"]["packages"][0]["version"] = version
    runtime = parse_runtime(json.dumps(document).encode())
    checksum = document["pre_commit"]["packages"][0]["artifacts"][0]["sha256"]
    assert runtime.requirements().decode().splitlines()[0] == (
        f"cfgv=={version} --hash=sha256:{checksum}"
    )


@pytest.mark.parametrize(
    "version",
    [
        "26.3.invalid",
        "01.2",
        "1.02",
        "0!1.0",
        "01!1.0",
        "1.0rc01",
        "1.0.post01",
        "1.0.dev01",
        "1.0\n",
        " 1.0",
        "1.0+local",
        "v1.0",
    ],
)
def test_runtime_refuses_a_noncanonical_package_version(version):
    document = committed_runtime()
    document["pre_commit"]["packages"][0]["version"] = version
    failure = refused(document)
    assert failure.code == "invalid_field"
    assert "package.version" in str(failure)


def test_runtime_refuses_a_missing_target():
    document = committed_runtime()
    del document["python"]["cpython-3.14.10-linux-x86_64-gnu"]
    assert refused(document).code == "invalid_field"


@pytest.mark.parametrize("target", ["windows-x86_64-none", None])
def test_runtime_refuses_records_for_other_targets(target):
    document = committed_runtime()
    record = document["python"].pop("cpython-3.14.10-linux-x86_64-gnu")
    if target is None:
        document["python"] = {}
    else:
        system, _, libc = target.split("-")
        document["python"]["cpython-3.14.10-" + target] = {
            **record,
            "os": system,
            "libc": libc,
        }
    failure = refused(document)
    assert failure.code == "invalid_field"


def test_runtime_refuses_mixed_python_versions():
    document = committed_runtime()
    record = document["python"].pop("cpython-3.14.10-linux-x86_64-gnu")
    document["python"]["cpython-3.14.9-linux-x86_64-gnu"] = {**record, "patch": 9}
    failure = refused(document)
    assert failure.code == "invalid_field"
    assert "one CPython version and build" in str(failure)


def test_runtime_refuses_an_unknown_field():
    document = {**committed_runtime(), "surprise": "value"}
    assert refused(document).code == "invalid_field"


def test_runtime_refuses_a_closure_without_its_pre_commit():
    document = committed_runtime()
    packages = document["pre_commit"]["packages"]
    document["pre_commit"]["packages"] = [
        p for p in packages if p["name"] != "pre-commit"
    ]
    failure = refused(document)
    assert failure.code == "invalid_field"
    assert "disagrees with its pin" in str(failure)


def test_hooks_need_the_pre_commit_that_renders_them(monkeypatch):
    policy = load_bundled_policy()
    source = BundledSource(policy.release)
    resolve(parse_manifest(render(("hygiene", "python", "secrets"), source)), policy)
    old = dataclasses.replace(load_runtime(), pre_commit="4.3.0")
    monkeypatch.setattr("gateseal.manifest.load_runtime", lambda: old)
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
    """Install a release at version whose runtime.lock pins pre_commit.

    Only the pin changes, so the recorded hashes belong to another version.
    """
    package = versioned_package(directory / "package", version)
    path = package / "src" / "gateseal" / "data" / "runtime.lock"
    document = json.loads(path.read_bytes())
    document["pre_commit"]["version"] = pre_commit
    for entry in document["pre_commit"]["packages"]:
        if entry["name"] == "pre-commit":
            entry["version"] = pre_commit
    path.write_text(json.dumps(document))
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
