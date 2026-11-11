import dataclasses
import platform

import pytest
from gateseal import bootstrap
from test_bootstrap_record import prepare


@pytest.mark.parametrize("content", [False, True], ids=["commit", "doctor"])
def test_an_attempt_prepared_on_another_platform_is_refused(tmp_path, content):
    """An attempt prepared on another platform needs a new activation.

    A Git directory that another machine shares, such as a Mac's mounted in a
    Linux container, holds attempts whose programs cannot run here.
    """
    record = prepare(tmp_path, tmp_path)
    here = platform.system() + "-" + platform.machine()
    other = "Linux-x86_64" if here == "Darwin-arm64" else "Darwin-arm64"
    with pytest.raises(bootstrap.BootstrapError) as raised:
        bootstrap.verify_runtime(
            dataclasses.replace(record, platform=other), content=content
        )
    assert raised.value.next_step == bootstrap.ACTIVATE
