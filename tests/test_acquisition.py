import os
from dataclasses import replace

import pytest
from gateseal.platforms import machine
from gateseal.policy import load_bundled_policy
from gateseal.tools.release_binary import mise_request
from installation import process


@pytest.mark.integration
def test_mise_checks_exact_betterleaks_archive_hash(tmp_path):
    release = load_bundled_policy().tools["betterleaks"].release
    target = machine()
    archive = release.platforms[target]
    wrong = {target: replace(archive, sha256="0" * 64)}
    request = mise_request("betterleaks", release.version, wrong)
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("MISE_")},
        "MISE_CACHE_DIR": str(tmp_path / "mise-cache"),
        "MISE_DATA_DIR": str(tmp_path / "data"),
        "MISE_STATE_DIR": str(tmp_path / "state"),
        "MISE_SYSTEM_DATA_DIR": str(tmp_path / "system"),
    }
    result = process(["mise", "--no-config", "install", request], tmp_path, env)
    assert result.returncode != 0
    assert "Checksum mismatch" in result.stderr, result.stderr
    assert "Expected: sha256:" + "0" * 64 in result.stderr
    assert archive.sha256 in result.stderr
    assert not list((tmp_path / "data").glob("installs/http-betterleaks-*/*"))
