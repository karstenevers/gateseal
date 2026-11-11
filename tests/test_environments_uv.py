"""The environment activation's uv runs in, and the package environments it builds."""

import base64
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

from gateseal.environments import package_environment, uv_environment
from gateseal.processes import find_tool


def test_uv_may_download_but_keeps_only_the_callers_caches(monkeypatch):
    for name, value in (
        ("UV_OFFLINE", "1"),
        ("UV_PYTHON_DOWNLOADS", "never"),
        ("UV_INDEX_URL", "https://mirror.invalid/simple"),
        ("UV_CACHE_DIR", "/caches/uv"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("UV_PYTHON_CACHE_DIR", raising=False)
    env = uv_environment()
    assert {key: value for key, value in env.items() if key.startswith("UV_")} == {
        "UV_CACHE_DIR": "/caches/uv",
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_HTTP_CONNECT_TIMEOUT": "10",
        "UV_HTTP_TIMEOUT": "30",
        "UV_HTTP_RETRIES": "1",
        "UV_NO_PROGRESS": "1",
    }


def wheel(directory):
    """Write a one-module wheel, as a package index would serve it."""
    name = "demo_tool-1.0-py3-none-any.whl"
    files = {
        "demo_tool.py": b"ANSWER = 42\n",
        "demo_tool-1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: demo-tool\nVersion: 1.0\n"
        ),
        "demo_tool-1.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
            b"Tag: py3-none-any\n"
        ),
    }
    record = []
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        record.append(f"{path},sha256={digest.decode()},{len(data)}")
    record.append("demo_tool-1.0.dist-info/RECORD,,")
    files["demo_tool-1.0.dist-info/RECORD"] = ("\n".join(record) + "\n").encode()
    path = directory / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, data in files.items():
            archive.writestr(member, data)
    return path


def test_a_package_environment_holds_the_hashed_requirements(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv cache"))
    built = wheel(tmp_path)
    requirements = tmp_path / "requirements.txt"
    checksum = hashlib.sha256(built.read_bytes()).hexdigest()
    requirements.write_text(f"demo-tool @ {built.as_uri()} --hash=sha256:{checksum}\n")
    destination = tmp_path / "environment"
    python = package_environment(
        find_tool("uv"),
        tmp_path,
        uv_environment() | {"UV_OFFLINE": "1"},
        sys.executable,
        destination,
        requirements,
    )
    assert Path(python).parent.parent == destination
    answer = subprocess.run(
        [python, "-I", "-c", "import demo_tool; print(demo_tool.ANSWER)"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert answer.stdout == "42\n"
