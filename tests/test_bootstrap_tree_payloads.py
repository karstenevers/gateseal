import os

import pytest
from gateseal import bootstrap


@pytest.mark.parametrize("content", [False, True], ids=["metadata", "content"])
def test_a_tree_that_holds_a_fifo_has_no_seal(tmp_path, content):
    root = tmp_path / "entry"
    root.mkdir()
    (root / "file").write_bytes(b"file\n")
    os.mkfifo(root / "pipe")
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.tree_digest(root, (root,), content=content)
