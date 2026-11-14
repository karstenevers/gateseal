import errno
from pathlib import Path

import pytest
from gateseal.paths import resolved

SOURCE = Path(__file__).parents[1] / "src" / "gateseal"


def test_a_symlink_loop_resolves_leniently_and_fails_strictly(tmp_path):
    loop = tmp_path.resolve() / "loop"
    loop.symlink_to(loop.name)
    assert resolved(loop / "file").parent == loop
    with pytest.raises(OSError) as raised:
        resolved(loop / "file", strict=True)
    assert raised.value.errno == errno.ELOOP


def test_a_path_resolves_through_its_links(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to("real")
    assert resolved(tmp_path / "link" / "file") == tmp_path.resolve() / "real" / "file"


def test_no_module_resolves_a_path_with_pathlib():
    """Before Python 3.13, Path.resolve raises RuntimeError on a symlink loop."""
    calls = [
        f"{path.relative_to(SOURCE)}:{number}"
        for path in sorted(SOURCE.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if ".resolve(" in line
    ]
    assert calls == []
