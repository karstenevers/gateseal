"""gateseal policy lock reads python-build-standalone's newest build name.

A fake upstream serves the files gateseal policy lock would download from
GitHub, so each test names exactly what the network returned.
"""

import json

import pytest
from black_policy import BUILD, PYTHON
from gateseal import hook_python
from gateseal.compiler_release import TARGETS
from gateseal.diagnostics import GatesealError


class Upstream:
    def __init__(self, files):
        self.files = files

    def tagged(self, repository, tag):
        return None

    def download(self, url):
        return self.files.get(url)


def latest(tag):
    return Upstream({hook_python.LATEST: json.dumps({"tag": tag}).encode()})


@pytest.mark.parametrize("tag", [20261026, "2026102", "202610260", "2026-10-", None])
def test_a_build_name_is_eight_digits(tag):
    with pytest.raises(GatesealError) as raised:
        hook_python.lock("3.15.2", None, latest(tag))
    assert (raised.value.code, raised.value.exit_code) == ("upstream_unavailable", 4)
    assert hook_python.LATEST in str(raised.value)


def test_the_newest_build_names_the_checksums_to_read():
    with pytest.raises(GatesealError) as raised:
        hook_python.lock("3.15.2", None, latest(BUILD))
    assert raised.value.code == "upstream_checksums_missing"
    assert BUILD in str(raised.value)


def test_a_target_gets_only_its_own_interpreter_record():
    python = hook_python.HookPython("3.15.2", PYTHON)
    for target in TARGETS.values():
        key = f"cpython-3.15.2-{target.python}"
        assert python.interpreter(target) == {key: PYTHON[key]}
