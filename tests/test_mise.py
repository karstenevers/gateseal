"""gateseal asks the mise on PATH for its version before a hook needs mise.

A shell script stands in for mise and records the environment it gets.
"""

from pathlib import Path

import pytest
from gateseal.tools.mise import require_mise
from stand_ins import executable
from test_lock_configure import refused


def path_with(tmp_path, monkeypatch, script):
    """Put a mise that runs script first on PATH, and return the environment."""
    mise = executable(tmp_path / "shims" / "mise", "#!/bin/sh\n" + script)
    monkeypatch.setenv("PATH", f"{mise.parent}:/usr/bin:/bin")
    return mise, {"PATH": f"{mise.parent}:/usr/bin:/bin", "HOME": str(tmp_path)}


def test_mise_reports_its_version_from_a_scratch_directory(tmp_path, monkeypatch):
    seen = tmp_path / "environment"
    mise, env = path_with(
        tmp_path, monkeypatch, f"env > {seen}\necho 2026.10.19 linux-x64\n"
    )
    assert require_mise(env) == str(mise)
    found = dict(line.split("=", 1) for line in seen.read_text().splitlines())
    scratch = Path(found["MISE_DATA_DIR"]).parent
    names = ("MISE_DATA_DIR", "MISE_STATE_DIR", "MISE_SYSTEM_DATA_DIR")
    assert [found[name] for name in names] == [
        str(scratch / "data"),
        str(scratch / "state"),
        str(scratch / "system"),
    ]
    assert (found["MISE_OFFLINE"], found["HOME"]) == ("1", str(tmp_path))
    assert not scratch.exists()


@pytest.mark.parametrize(
    "printed", ["echo 2026.10.18 linux-x64\n", "", "echo unknown\n"]
)
def test_an_older_or_unreadable_mise_version_is_refused(tmp_path, monkeypatch, printed):
    _, env = path_with(tmp_path, monkeypatch, printed)
    failure = refused(require_mise, env)
    assert (failure.code, failure.exit_code, str(failure)) == (
        "mise_version_mismatch",
        4,
        "gateseal requires mise 2026.10.19 or later.",
    )
