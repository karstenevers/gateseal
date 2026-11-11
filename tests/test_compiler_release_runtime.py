"""runtime.lock: the CPython records and the pre-commit closure a release carries.

Each test edits one field of the committed runtime.lock, so every other field
stays as a release writes it, and checks what parse_runtime refuses.
"""

import json
from importlib.resources import files

import pytest
from gateseal.compiler_release import parse_runtime
from gateseal.diagnostics import GatesealError

LINUX = "cpython-3.14.10-linux-x86_64-gnu"
ORDINARY = "ordinary"
STABLE = "stable"
INTEGERS = "integers"
OFFICIAL = "official"
UNSAFE = "Unsafe"
NAMES = "normalized"
WHEEL = "filename"
PYPI = "PyPI"


def committed():
    return json.loads(files("gateseal").joinpath("data", "runtime.lock").read_bytes())


def refusal(document):
    with pytest.raises(GatesealError) as failure:
        parse_runtime(json.dumps(document).encode())
    assert failure.value.code == "invalid_field"
    return str(failure.value)


def test_a_cpython_release_with_a_zero_component_is_accepted():
    document = committed()
    document["python"] = {
        key.replace("3.14.10", "3.14.0"): record | {"patch": 0}
        for key, record in document["python"].items()
    }
    runtime = parse_runtime(json.dumps(document).encode())
    assert set(runtime.python) == {
        "cpython-3.14.0-darwin-aarch64-none",
        "cpython-3.14.0-linux-x86_64-gnu",
    }


def url(edit):
    def change(record):
        record["url"] = edit(record["url"])

    return change


def setting(key, value):
    def change(record):
        record[key] = value

    return change


@pytest.mark.parametrize(
    "change,subject",
    [
        (setting("name", "pypy"), ORDINARY),
        (setting("arch", {"family": "x86_64", "variant": "v3"}), ORDINARY),
        (setting("variant", "debug"), STABLE),
        (setting("prerelease", "rc1"), STABLE),
        (setting("minor", -1), INTEGERS),
        (setting("minor", "14"), INTEGERS),
        (setting("major", True), INTEGERS),
        (url(lambda u: u.removesuffix(".tar.gz") + ".zip"), OFFICIAL),
        (url(lambda u: u.replace("/20261026/", "/20250101/")), OFFICIAL),
        (url(lambda u: u + "?mirror=.tar.gz"), UNSAFE),
        (url(lambda u: u + "#.tar.gz"), UNSAFE),
        (url(lambda u: u.replace("/20261026/", "/20261026/../20261026/")), UNSAFE),
        (setting("sha256", "A" * 64), "interpreter.sha256"),
    ],
    ids=[
        "pypy",
        "arch variant",
        "debug variant",
        "prerelease",
        "negative minor",
        "text minor",
        "boolean major",
        "zip",
        "other build",
        "query",
        "fragment",
        "parent segment",
        "uppercase digest",
    ],
)
def test_runtime_python_is_one_official_stable_cpython_archive(change, subject):
    document = committed()
    change(document["python"][LINUX])
    assert subject in refusal(document)


def package(document, name):
    [found] = [p for p in document["pre_commit"]["packages"] if p["name"] == name]
    return found


def renamed(name):
    def change(document):
        package(document, "distlib")["name"] = name

    return change


def artifact(key, edit):
    def change(document):
        item = package(document, "cfgv")["artifacts"][0]
        item[key] = edit(item[key])

    return change


def pre_commit_version(document):
    package(document, "pre-commit")["version"] = "4.6.0"


@pytest.mark.parametrize(
    "change,subject",
    [
        (renamed("Distlib"), NAMES),
        (renamed("cfgv"), NAMES),
        (artifact("filename", lambda f: "cfgv-3.5.0.tar.gz"), WHEEL),
        (artifact("filename", lambda f: "wheels/" + f), WHEEL),
        (
            artifact(
                "url", lambda u: u.replace("files.pythonhosted.org", "pypi.example")
            ),
            PYPI,
        ),
        (artifact("url", lambda u: u.replace("cfgv-3.5.0-", "cfgv-3.4.0-")), PYPI),
        (artifact("sha256", lambda s: s.upper()), "artifact.sha256"),
        (pre_commit_version, "closure"),
    ],
    ids=[
        "unnormalized name",
        "name twice",
        "source archive",
        "wheel in a folder",
        "other index",
        "url of another wheel",
        "uppercase digest",
        "pre-commit at another version",
    ],
)
def test_runtime_pre_commit_closure_is_hash_pinned_pypi_wheels(change, subject):
    document = committed()
    change(document)
    assert subject in refusal(document)


def test_runtime_names_the_command_that_generated_it():
    document = committed() | {"generated_by": ""}
    assert "runtime.generated_by" in refusal(document)
