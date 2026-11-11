import pytest
from gateseal.diagnostics import GatesealError
from gateseal.managed_files import write_atomic


def test_symlink_parent_never_writes_outside(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(GatesealError):
        write_atomic(tmp_path / "link" / "file", b"bad")
    assert not (outside / "file").exists()
    write_atomic(outside / "file", b"good")
    assert (outside / "file").read_bytes() == b"good"
