"""runtime.lock's CPython records: each key names its record, each build a date.

Every check in validate_python refuses with invalid_field, so each test pairs a
refused Linux record with one that differs only in the field under test and is
accepted.
"""

import pytest
from gateseal.compiler_release import validate_python
from gateseal.diagnostics import GatesealError
from test_compiler_release_runtime import LINUX, committed


def refused(records):
    with pytest.raises(GatesealError) as failure:
        validate_python(records)
    assert failure.value.code == "invalid_field"


@pytest.mark.parametrize(
    "change,key",
    [
        ({"patch": 9}, "cpython-3.14.9-linux-x86_64-gnu"),
        ({"os": "freebsd"}, "cpython-3.14.10-freebsd-x86_64-gnu"),
        (
            {"arch": {"family": "aarch64", "variant": None}},
            "cpython-3.14.10-linux-aarch64-gnu",
        ),
        ({"libc": "musl"}, "cpython-3.14.10-linux-x86_64-musl"),
    ],
    ids=["release", "os", "arch", "libc"],
)
def test_a_python_key_names_its_records_release_and_target(change, key):
    records = committed()["python"]
    record = records.pop(LINUX) | change
    refused(records | {LINUX: record})
    assert key in validate_python(records | {key: record})


@pytest.mark.parametrize("build", ["2026102", "202610260", "2026-10-26", "2026102a"])
def test_an_interpreter_build_is_an_eight_digit_date(build):
    records = committed()["python"]
    linux = records[LINUX]

    def built(build):
        url = linux["url"].replace("/20261026/", f"/{build}/")
        return records | {LINUX: linux | {"build": build, "url": url}}

    refused(built(build))
    assert validate_python(built("20261027"))[LINUX]["build"] == "20261027"
