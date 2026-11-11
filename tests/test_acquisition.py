import base64
import hashlib
import io
import json
import netrc
import os
import platform
import sys
import urllib.request
import zipfile
from email.parser import Parser
from urllib.parse import urlsplit

import pytest
from conftest import TRACKED
from gateseal.compiler_release import TARGETS, load_runtime
from gateseal.policy import load_bundled_policy
from gateseal.tools.release_binary import NativeTool
from installation import PACKAGE, process, success


def environment(tmp_path, name):
    destination = tmp_path / name
    env = dict(
        os.environ,
        UV_CACHE_DIR=str(tmp_path / (name + "-cache")),
        UV_HTTP_CONNECT_TIMEOUT="10",
        UV_HTTP_TIMEOUT="30",
        UV_HTTP_RETRIES="0",
    )
    success(
        [
            "uv",
            "venv",
            "--no-config",
            "--no-python-downloads",
            "--python",
            sys.executable,
            destination,
        ],
        tmp_path,
        env,
    )
    python = success(
        ["uv", "python", "find", "--no-config", "--no-python-downloads", destination],
        tmp_path,
        env,
    ).stdout.strip()
    return python, env


def install(python, env, root, source, checksum):
    requirements = root / "compiler.txt"
    requirements.write_text(f"gateseal @ {source} --hash=sha256:{checksum}\n")
    return process(
        [
            "uv",
            "pip",
            "install",
            "--no-config",
            "--python",
            python,
            "--no-deps",
            "--only-binary",
            ":all:",
            "--require-hashes",
            "-r",
            requirements,
        ],
        root,
        env,
    )


@pytest.mark.integration
def test_uv_rejects_local_wheel_wrong_hash(installed, tmp_path):
    python, env = environment(tmp_path, "local-negative")
    result = install(python, env, tmp_path, installed.wheel.as_uri(), "0" * 64)
    assert result.returncode != 0 and "Hash mismatch" in result.stderr
    assert (
        process([python, "-I", "-B", "-c", "import gateseal"], tmp_path, env).returncode
        != 0
    )


@pytest.mark.integration
def test_uv_checks_exact_interpreter_archive_hash(tmp_path):
    target = TARGETS[(platform.system(), platform.machine())]
    [(key, record)] = load_runtime().interpreter(target).items()
    metadata = tmp_path / "wrong-python.json"
    metadata.write_text(json.dumps({key: {**record, "sha256": "0" * 64}}))
    env = dict(os.environ, UV_CACHE_DIR=str(tmp_path / "uv-cache"))
    result = process(
        [
            "uv",
            "python",
            "install",
            "--no-config",
            "--no-bin",
            "--install-dir",
            tmp_path / "python",
            "--python-downloads-json-url",
            metadata,
            key,
        ],
        tmp_path,
        env,
    )
    assert result.returncode != 0 and "Hash mismatch" in result.stderr
    assert not list((tmp_path / "python").glob("cpython-*"))


@pytest.mark.integration
def test_mise_checks_exact_betterleaks_archive_hash(tmp_path):
    release = load_bundled_policy().tools["betterleaks"].release
    target = TARGETS[(platform.system(), platform.machine())]
    archive = release.platforms[target.mise]
    tool = NativeTool(
        "betterleaks", release.version, archive.url, "0" * 64, archive.executable
    )
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("MISE_")},
        "MISE_CACHE_DIR": str(tmp_path / "mise-cache"),
        "MISE_DATA_DIR": str(tmp_path / "data"),
        "MISE_STATE_DIR": str(tmp_path / "state"),
        "MISE_SYSTEM_DATA_DIR": str(tmp_path / "system"),
    }
    result = process(["mise", "--no-config", "install", tool.spec], tmp_path, env)
    assert result.returncode != 0
    assert "Checksum mismatch" in result.stderr, result.stderr
    assert "Expected: sha256:" + "0" * 64 in result.stderr
    assert archive.sha256 in result.stderr
    assert not list((tmp_path / "data").glob("installs/http-betterleaks/*/*"))


@pytest.mark.network
@pytest.mark.parametrize("case", ["correct", "wrong-hash", "unavailable", "anonymous"])
def test_private_uv_artifact_source(tmp_path, case):
    artifact = json.loads((PACKAGE / "tests/artifact-source.json").read_bytes())
    python, env = environment(tmp_path, case)
    url, checksum = artifact["url"], artifact["sha256"]
    if case == "wrong-hash":
        checksum = "0" * 64
    elif case == "unavailable":
        parts = url.split("/")
        parts[5] = "0" * 40
        url = "/".join(parts)
    elif case == "anonymous":
        env["NETRC"] = str(tmp_path / "no-credentials")
    result = install(python, env, tmp_path, url, checksum)
    if case == "correct":
        assert result.returncode == 0, result.stdout + result.stderr
        offline = success(
            [
                "uv",
                "run",
                "--no-config",
                "--no-project",
                "--offline",
                "--no-python-downloads",
                "--python",
                python,
                python,
                "-I",
                "-B",
                "-m",
                "gateseal",
                "--version",
            ],
            tmp_path,
            env,
        )
        assert offline.stdout.strip() == "0.1.0"
        metadata = success(
            [
                python,
                "-I",
                "-B",
                "-c",
                "from importlib.metadata import distribution; "
                "print(distribution('gateseal').read_text('direct_url.json'))",
            ],
            tmp_path,
            env,
        )
        assert json.loads(metadata.stdout)["url"] == url
    else:
        assert result.returncode != 0
        assert (
            "Hash mismatch" in result.stderr
            if case == "wrong-hash"
            else "404" in result.stderr
        )


def fetched(url):
    """Download url with the NETRC credentials that uv reads too."""
    login, _, password = netrc.netrc(os.environ["NETRC"]).authenticators(
        urlsplit(url).hostname
    )
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def without_readme(wheel):
    """Return the wheel's files with RECORD and the README text left out."""
    with zipfile.ZipFile(io.BytesIO(wheel)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    for name, data in files.items():
        if name.endswith(".dist-info/RECORD"):
            files[name] = None
        elif name.endswith(".dist-info/METADATA"):
            headers = Parser().parsestr(data.decode()).items()
            files[name] = [h for h in headers if h[0] != "Description-Content-Type"]
    return files


@pytest.mark.network
def test_private_fixture_is_the_built_wheel(installed):
    """A README edit changes the wheel's metadata, but needs no new fixture."""
    artifact = json.loads((PACKAGE / "tests/artifact-source.json").read_bytes())
    fixture = fetched(artifact["url"])
    assert hashlib.sha256(fixture).hexdigest() == artifact["sha256"]
    assert without_readme(fixture) == without_readme(installed.wheel.read_bytes())


@pytest.mark.network
def test_private_source_runs_complete_workflow(tmp_path, consumer):
    artifact = json.loads((PACKAGE / "tests/artifact-source.json").read_bytes())
    python, env = environment(tmp_path, "private-workflow")
    result = install(python, env, tmp_path, artifact["url"], artifact["sha256"])
    assert result.returncode == 0, result.stdout + result.stderr
    command = [python, "-I", "-B", "-m", "gateseal", "--repo", consumer, "--json"]
    success(
        [
            *command,
            "configure",
            "--capability",
            "hygiene",
            "--compiler-source",
            artifact["url"],
            "--compiler-sha256",
            artifact["sha256"],
        ],
        consumer,
        env,
    )
    success([*command, "activate"], consumer, env)
    success(["git", "add", *TRACKED], consumer)
    success(["git", "commit", "-m", "Use verified private compiler"], consumer)
    success([*command, "run"], consumer, env)
