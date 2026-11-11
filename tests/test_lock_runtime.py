import importlib.util
import json
import subprocess
import sys
from urllib.parse import quote, unquote

import pytest
from gateseal.compiler_release import interpreter_records
from gateseal.diagnostics import GatesealError
from installation import PACKAGE

SCRIPT = PACKAGE / "scripts" / "lock_runtime.py"
COMMITTED = PACKAGE / "src" / "gateseal" / "data" / "runtime.lock"
DARWIN, LINUX = "aarch64-apple-darwin", "x86_64-unknown-linux-gnu"


def load_script():
    spec = importlib.util.spec_from_file_location("lock_runtime", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lock_runtime = load_script()


def committed():
    return json.loads(COMMITTED.read_bytes())


def request():
    return lock_runtime.parse_request((PACKAGE / "runtime.toml").read_bytes())


def sums(records):
    """Return a SHA256SUMS text with records' archives among other files."""
    lines = [
        f"{'0' * 64}  cpython-3.14.10+20261026-x86_64-unknown-linux-gnu-full.tar.zst"
    ]
    for record in records.values():
        lines.append(f"{record['sha256']}  {unquote(record['url'].split('/')[-1])}")
    return "\n".join(lines) + "\n"


def wheel(filename, digest):
    url = f"https://files.pythonhosted.org/packages/ab/cd/{quote(filename)}"
    return f'{{ url = "{url}", size = 1, hashes = {{ sha256 = "{digest}" }} }}'


def pylock(*packages):
    """Return a pylock.toml of (name, version, wheels) entries, as uv writes it."""
    text = 'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3.14.10"\n'
    for name, version, wheels in packages:
        text += f'\n[[packages]]\nname = "{name}"\nversion = "{version}"\n'
        if wheels:
            text += f"wheels = [{', '.join(wheel(*w) for w in wheels)}]\n"
    return text.encode()


PRE_COMMIT = (
    "pre-commit",
    "4.7.0",
    [("pre_commit-4.7.0-py2.py3-none-any.whl", "a" * 64)],
)
PINS = {"pre-commit": "4.7.0", "pyyaml": "6.0.3"}


def refusal(call, *arguments):
    with pytest.raises(lock_runtime.Refusal) as failure:
        call(*arguments)
    return str(failure.value)


def test_committed_lock_renders_from_its_parts():
    document = committed()
    rendered = lock_runtime.render(
        request(),
        interpreter_records(sums(document["python"]), "3.14.10", "20261026"),
        document["pre_commit"]["packages"],
    )
    assert rendered == COMMITTED.read_bytes()


@pytest.mark.parametrize(
    "requirement",
    [
        "pre-commit>=4.7.0",
        "pre-commit == 4.7.0",
        "Pre-Commit==4.7.0",
        "pre_commit==4.7.0",
        "pre-commit==4.7.0 --hash=sha256:" + "a" * 64,
        "pre-commit==4.7.0; python_version >= '3.14'",
    ],
)
def test_requirements_must_be_exact_normalized_pins(requirement):
    text = (PACKAGE / "runtime.toml").read_text()
    changed = text.replace('"pre-commit==4.7.0"', json.dumps(requirement))
    assert "is not a name==version pin" in refusal(
        lock_runtime.parse_request, changed.encode()
    )


def test_runtime_toml_is_read_strictly():
    text = (PACKAGE / "runtime.toml").read_text()
    assert "requires exactly" in refusal(
        lock_runtime.parse_request, ('surprise = "x"\n' + text).encode()
    )
    unpinned = text.replace('    "pre-commit==4.7.0",\n', "")
    assert "pins no pre-commit" in refusal(
        lock_runtime.parse_request, unpinned.encode()
    )


def test_each_target_needs_exactly_one_sums_line():
    text = sums(committed()["python"])
    linux = next(
        line for line in text.splitlines() if LINUX in line and "install_only" in line
    )
    missing = text.replace(linux + "\n", "")
    for sums_text, found in (
        (missing, "has 0 lines for"),
        (text + linux + "\n", "has 2 lines for"),
    ):
        with pytest.raises(GatesealError) as failure:
            interpreter_records(sums_text, "3.14.10", "20261026")
        assert found in str(failure.value)


def test_an_unpinned_package_is_refused():
    resolution = pylock(PRE_COMMIT, ("pyyaml", "6.0.3", [("pyyaml.whl", "b" * 64)]))
    added = pylock(PRE_COMMIT, ("virtualenv", "21.15.0", [("v.whl", "c" * 64)]))
    message = refusal(lock_runtime.packages, {DARWIN: resolution, LINUX: added}, PINS)
    assert message == (
        f"The resolution for {LINUX} adds virtualenv 21.15.0, which runtime.toml "
        "does not pin."
    )


def test_a_version_other_than_its_pin_is_refused():
    resolution = pylock(PRE_COMMIT, ("pyyaml", "6.0.2", [("pyyaml.whl", "b" * 64)]))
    assert "selects pyyaml 6.0.2, but runtime.toml pins 6.0.3" in refusal(
        lock_runtime.packages, {DARWIN: resolution}, PINS
    )


def test_a_pin_missing_from_a_resolution_is_refused():
    assert f"The resolution for {LINUX} lacks pyyaml" in refusal(
        lock_runtime.packages, {LINUX: pylock(PRE_COMMIT)}, PINS
    )


def test_a_package_without_a_wheel_is_refused():
    resolution = pylock(PRE_COMMIT, ("pyyaml", "6.0.3", []))
    assert f"pyyaml 6.0.3 has no wheel for {DARWIN}" in refusal(
        lock_runtime.packages, {DARWIN: resolution}, PINS
    )


def test_one_wheel_with_two_digests_is_refused():
    universal = ("pyyaml-6.0.3-py3-none-any.whl",)
    darwin = pylock(PRE_COMMIT, ("pyyaml", "6.0.3", [(*universal, "b" * 64)]))
    linux = pylock(PRE_COMMIT, ("pyyaml", "6.0.3", [(*universal, "c" * 64)]))
    assert "pyyaml-6.0.3-py3-none-any.whl has two SHA-256 digests" in refusal(
        lock_runtime.packages, {DARWIN: darwin, LINUX: linux}, PINS
    )


def test_wheels_merge_across_targets():
    macos = "pyyaml-6.0.3-cp314-cp314-macosx_11_0_arm64.whl"
    manylinux = "pyyaml-6.0.3-cp314-cp314-manylinux_2_17_x86_64.whl"
    darwin = pylock(("pyyaml", "6.0.3", [(macos, "b" * 64)]), PRE_COMMIT)
    linux = pylock(PRE_COMMIT, ("pyyaml", "6.0.3", [(manylinux, "c" * 64)]))
    closure = lock_runtime.packages({DARWIN: darwin, LINUX: linux}, PINS)
    assert [p["name"] for p in closure] == ["pre-commit", "pyyaml"]
    assert len(closure[0]["artifacts"]) == 1
    assert [a["filename"] for a in closure[1]["artifacts"]] == [macos, manylinux]
    assert closure[1]["artifacts"][1] == {
        "filename": manylinux,
        "sha256": "c" * 64,
        "url": f"https://files.pythonhosted.org/packages/ab/cd/{manylinux}",
    }


def test_a_refusal_writes_nothing(tmp_path, monkeypatch, capsys):
    document = committed()
    monkeypatch.setattr(lock_runtime, "download", lambda url: sums(document["python"]))
    monkeypatch.setattr(
        lock_runtime,
        "resolve",
        lambda request, platform: pylock(("surprise", "1.0.0", [("s.whl", "c" * 64)])),
    )
    output = tmp_path / "runtime.lock"
    assert lock_runtime.main(["--output", str(output)]) == 1
    assert capsys.readouterr().err.startswith(
        f"lock_runtime: The resolution for {DARWIN} adds surprise 1.0.0"
    )
    assert not output.exists()


@pytest.mark.network
def test_two_runs_reproduce_the_committed_runtime(tmp_path):
    for name in ("first.lock", "second.lock"):
        result = subprocess.run(
            [sys.executable, SCRIPT, "--output", tmp_path / name],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / name).read_bytes() == COMMITTED.read_bytes()
