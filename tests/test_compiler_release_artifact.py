"""The compiler a Lock pins: its wheel's name, digest and source, and the wheel.

Each wheel is a zip that the test writes in tmp_path with the METADATA that
verify_wheel reads, so no test builds or downloads a real compiler.
"""

import hashlib
import zipfile

import pytest
from gateseal import __version__
from gateseal.compiler_release import (
    CompilerArtifact,
    artifact_from_flags,
    verify_wheel,
)
from gateseal.diagnostics import GatesealError

WHEEL = "gateseal-0.1.0-py3-none-any.whl"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
HOST = "https://raw.githubusercontent.com"
REPOSITORY = f"{HOST}/example/wheels"
RAW = f"{REPOSITORY}/{COMMIT}/artifacts/{WHEEL}"
SHA = "a" * 64
MISMATCH = "dependencies"
CURRENT = f"gateseal-{__version__}-py3-none-any.whl"


def document(kind="local-wheel", location=f"/wheels/{WHEEL}", **changes):
    """Return the Lock's compiler record of the 0.1.0 wheel, with changes."""
    return {
        "name": "gateseal",
        "version": "0.1.0",
        "filename": WHEEL,
        "sha256": SHA,
        "source": {"kind": kind, "location": location},
    } | changes


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


def metadata(name="gateseal", version="0.1.0", extra=""):
    return f"Metadata-Version: 2.4\nName: {name}\nVersion: {version}\n{extra}"


def wheel(directory, release="0.1.0", name=None, members=None):
    """Write release's wheel, by default with its METADATA and its own name."""
    directory.mkdir(parents=True, exist_ok=True)
    if members is None:
        members = {f"gateseal-{release}.dist-info/METADATA": metadata(version=release)}
    path = directory / (name or f"gateseal-{release}-py3-none-any.whl")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("gateseal/__init__.py", "")
        for member, text in members.items():
            archive.writestr(member, text)
    return path


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pinned(path, release="0.1.0", sha=None):
    """Return the artifact that pins path's bytes as the wheel of release."""
    name = f"gateseal-{release}-py3-none-any.whl"
    return CompilerArtifact(
        "local-wheel", str(path), name, sha or checksum(path), release
    )


@pytest.mark.parametrize(
    "kind,location", [("local-wheel", f"/wheels/{WHEEL}"), ("https-wheel", RAW)]
)
def test_a_compiler_record_parses_to_the_artifact_it_documents(kind, location):
    artifact = CompilerArtifact.parse(document(kind, location))
    assert artifact == CompilerArtifact(kind, location, WHEEL, SHA, "0.1.0")
    assert artifact.document() == document(kind, location)


@pytest.mark.parametrize(
    "changes,code,named",
    [
        ({"name": "pip"}, "invalid_field", ["gateseal"]),
        (
            {"filename": "gateseal-0.2.0-py3-none-any.whl"},
            "invalid_field",
            ["filename"],
        ),
        (
            {"source": {"kind": "s3-wheel", "location": RAW}},
            "unsupported_source",
            ["local-wheel", "https-wheel"],
        ),
        (
            {"source": {"kind": "local-wheel", "location": f"wheels/{WHEEL}"}},
            "invalid_field",
            [f"wheels/{WHEEL}", "absolute"],
        ),
        (
            {"source": {"kind": "local-wheel", "location": "/wheels/gateseal.whl"}},
            "invalid_field",
            ["/wheels/gateseal.whl", WHEEL],
        ),
    ],
    ids=["name", "filename", "transport", "relative wheel", "wheel of another name"],
)
def test_a_compiler_record_names_gateseal_and_its_own_wheel(changes, code, named):
    failure = refused(CompilerArtifact.parse, document(**changes))
    assert failure.code == code
    assert all(value in str(failure) for value in named)


@pytest.mark.parametrize(
    "path",
    [
        f"/example/wheels/{COMMIT}/{WHEEL}",
        f"/team/gateseal-fixtures/{COMMIT}/artifacts/{WHEEL}",
        f"/Team-1/wheels.private_2/{COMMIT}/a/b.c/{WHEEL}",
    ],
    ids=["no folder", "other repository", "nested folders"],
)
def test_an_https_compiler_may_come_from_any_github_repository(path):
    artifact = CompilerArtifact.parse(document("https-wheel", HOST + path))
    assert artifact.location == HOST + path


@pytest.mark.parametrize(
    "location",
    [
        RAW.replace("https://", "http://"),
        RAW.replace("raw.githubusercontent.com", "github.com"),
        RAW.replace("https://", "https://token@"),
        RAW.replace("githubusercontent.com", "githubusercontent.com:443"),
        RAW + "?token=secret",
        RAW + "#latest",
        RAW.replace(COMMIT, "main"),
        RAW.replace(COMMIT, f"refs/heads/main/{COMMIT}"),
        RAW.replace(COMMIT, COMMIT[:12]),
        RAW.replace("/artifacts/", "/artifacts/../"),
        RAW.replace("/artifacts/", "/./"),
        RAW.replace("/wheels/", "/../"),
        RAW.replace("/artifacts/", "/%2e%2e/"),
        RAW.replace("/example/", "/-example/"),
        RAW.replace(WHEEL, "gateseal-0.2.0-py3-none-any.whl"),
        "https://[raw.githubusercontent.com/artifacts/" + WHEEL,
    ],
    ids=[
        "http",
        "other host",
        "credentials",
        "port",
        "query",
        "fragment",
        "branch",
        "ref before the commit",
        "short commit",
        "parent segment",
        "current segment",
        "parent repository",
        "encoded parent",
        "invalid owner",
        "other wheel",
        "unparsable",
    ],
)
def test_an_https_compiler_is_one_immutable_raw_file(location):
    failure = refused(CompilerArtifact.parse, document("https-wheel", location))
    assert failure.code == "invalid_field"
    assert "URL" in str(failure)


def test_a_wheel_is_checked_and_returned_by_its_resolved_path(tmp_path):
    real = wheel(tmp_path.resolve() / "downloads")
    (tmp_path / "link").symlink_to(real.parent)
    assert verify_wheel(tmp_path / "link" / WHEEL, pinned(real)) == real


def test_a_wheel_must_resolve_to_the_locked_file_name(tmp_path):
    real = wheel(tmp_path.resolve() / "downloads", name="renamed.whl")
    (tmp_path / WHEEL).symlink_to(real)
    failure = refused(verify_wheel, tmp_path / WHEEL, pinned(real))
    assert failure.code == "invalid_artifact"
    assert str(real) in str(failure)
    assert WHEEL in str(failure)


def test_a_missing_wheel_names_its_path(tmp_path):
    path = tmp_path.resolve() / WHEEL
    failure = refused(verify_wheel, path, CompilerArtifact.parse(document()))
    assert (failure.code, failure.exit_code) == ("missing_artifact", 4)
    assert str(path) in str(failure)


def test_a_wheel_must_have_its_locked_digest(tmp_path):
    path = wheel(tmp_path.resolve())
    failure = refused(verify_wheel, path, pinned(path, sha="0" * 64))
    assert (failure.code, failure.exit_code) == ("artifact_hash_mismatch", 4)
    assert "SHA-256" in str(failure)


@pytest.mark.parametrize(
    "members,subject",
    [
        ({}, "distribution"),
        (
            {
                "gateseal-0.1.0.dist-info/METADATA": metadata(),
                "vendored-1.0.dist-info/METADATA": metadata("vendored", "1.0"),
            },
            "distribution",
        ),
        ({"pip-0.1.0.dist-info/METADATA": metadata(name="pip")}, MISMATCH),
        ({"gateseal-0.1.0.dist-info/METADATA": metadata(version="0.2.0")}, MISMATCH),
        (
            {
                "gateseal-0.1.0.dist-info/METADATA": metadata(
                    extra="Requires-Dist: pyyaml\n"
                )
            },
            MISMATCH,
        ),
    ],
    ids=["no metadata", "two distributions", "name", "version", "dependency"],
)
def test_a_wheel_holds_only_the_locked_compiler(tmp_path, members, subject):
    path = wheel(tmp_path.resolve(), members=members)
    failure = refused(verify_wheel, path, pinned(path))
    assert failure.code == "invalid_artifact"
    assert subject in str(failure)


def test_a_file_that_is_no_zip_cannot_be_inspected(tmp_path):
    path = tmp_path.resolve() / WHEEL
    path.write_bytes(b"not a zip")
    failure = refused(verify_wheel, path, pinned(path))
    assert failure.code == "invalid_artifact"
    assert "inspect" in str(failure)


def test_a_wheel_flag_pins_the_absolute_path_of_a_checked_wheel(tmp_path, monkeypatch):
    path = wheel(tmp_path.resolve(), __version__)
    monkeypatch.chdir(path.parent)
    assert artifact_from_flags(CURRENT, None, checksum(path)) == CompilerArtifact(
        "local-wheel", str(path), CURRENT, checksum(path), __version__
    )


def test_a_wheel_flag_refuses_a_wheel_with_another_digest(tmp_path):
    path = wheel(tmp_path.resolve(), __version__)
    failure = refused(artifact_from_flags, str(path), None, "0" * 64)
    assert failure.code == "artifact_hash_mismatch"


def test_a_source_flag_pins_its_url_without_reading_it():
    url = RAW.replace(WHEEL, CURRENT)
    assert artifact_from_flags(None, url, SHA) == CompilerArtifact(
        "https-wheel", url, CURRENT, SHA, __version__
    )


@pytest.mark.parametrize(
    "given,source,sha",
    [(CURRENT, RAW, SHA), (None, None, SHA), (CURRENT, None, None), (None, RAW, None)],
    ids=[
        "wheel and source",
        "neither",
        "wheel without digest",
        "source without digest",
    ],
)
def test_flags_name_one_compiler_and_its_digest(given, source, sha):
    failure = refused(artifact_from_flags, given, source, sha)
    assert failure.code == "missing_artifact"
    flags = ("--compiler-wheel", "--compiler-source", "--compiler-sha256")
    assert all(flag in str(failure) for flag in flags)


def test_a_source_of_another_release_than_the_compilers_is_refused():
    url = RAW.replace(WHEEL, "gateseal-99.0.0-py3-none-any.whl")
    failure = refused(artifact_from_flags, None, url, SHA)
    assert failure.code == "invalid_field"
    assert "URL" in str(failure)


def test_without_a_release_the_wheels_name_gives_it(tmp_path):
    path = wheel(tmp_path.resolve(), "0.2.10")
    assert artifact_from_flags(str(path), None, checksum(path), release=None) == pinned(
        path, "0.2.10"
    )


def test_without_a_release_a_name_that_holds_none_is_refused():
    url = RAW.replace(WHEEL, "gateseal-latest.whl")
    failure = refused(artifact_from_flags, None, url, SHA, None)
    assert failure.code == "invalid_artifact"
    assert url in str(failure)
    assert "gateseal-<release>-py3-none-any.whl" in str(failure)
