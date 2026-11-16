import hashlib
import re
from importlib.resources import files

import pytest
from conftest import PIN
from gateseal.diagnostics import GatesealError
from gateseal.environments import tool_environment
from gateseal.lock import compile_lock
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy
from gateseal.tools import built_in_tools, load_definition
from gateseal.tools.model import Executable, Origin
from gateseal.validation import canonical

BETTERLEAKS_1_8 = (
    '\n[exceptions.betterleaks]\nversion = "1.8.0"\nreason = "r"\n'
    "review_by = 2026-12-31\n"
)
EXCLUDE = '\n[choices.hygiene]\nexclude = ["vendor", "docs/gen.d"]\n'
CONFIG = ".pre-commit-config.yaml"
RUFF = ".gateseal/ruff.toml"
RULES = ".gateseal/betterleaks.toml"
REPORT = ".gateseal/betterleaks-report.tmpl"
HISTORY_REPORT = ".gateseal/betterleaks-history-report.tmpl"
SECRETS = {
    RULES: "183ff997bc28f117c430b0b47903b9ddc92f697e8d421dc126e191fdc583c26e",
    REPORT: "e90202b5add329ed4cc8e507dacdda7bbdc9cb72b37aa717a8f31897bf53cdf9",
    HISTORY_REPORT: (
        "6a78914b3697c6e2a5baa53330b760938a191b367afc428947eb9ca4465c6aa6"
    ),
}
PYTHON = {RUFF: "ffcf7d3c282f374519e8110da535fbde8e4d9dd0d81b7dad32db64a6979f8c2c"}
ALL = "6edbc51f7109342deb3f7caf0fca05e49f7dbf1c70171fddfe6c2ebbec439191"

GOLDEN = {
    ("hygiene",): {
        "gateseal.lock": (
            "eba63952ea0846265461a69889d915af17a78f3315dfbef7a9c2523a21ee7853"
        ),
        CONFIG: "8c5cad35ae3a2f3e9090967cbb3bf10153977d424e757041c4a4b341cf7b037b",
    },
    ("hygiene", "python"): {
        "gateseal.lock": (
            "c05e62c2da2e1f356941db67f9416be95581f528b96ad9bd7daf64b4c57503ee"
        ),
        CONFIG: "0b1cc4d5b9a12df9e44c94893c455d35b6a087c44c95143f0334148d8e2cf451",
        **PYTHON,
    },
    ("hygiene", "secrets"): {
        "gateseal.lock": (
            "33e99b751525ba4a4878f353ad7cc5909fee4da32d6d9227484dc5881e420bdf"
        ),
        CONFIG: "539062edebd6998b5a2f2050ffe4e63897e8c7d7b221435cee1a65325590d3f3",
        **SECRETS,
    },
    ("hygiene", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "8304ce3029bfd34e52e94e8e9eeb19066f25fac52d644203aafab7ff1b7acb9d"
        ),
        CONFIG: "11961c1b20fc0c5bd290e7d76bf99f3e3e048a7e7facc671fc7ab9e683d90251",
        **SECRETS,
    },
    ("hygiene", "python", "secrets"): {
        "gateseal.lock": (
            "b542693f0ce11c2fb3825d7feb914d7d17be64cab44fe11ec05ab3203cddd856"
        ),
        CONFIG: "6be995a52e003a9e380b0c5db4e523eaf2f0ab470b60e4870780f9863c551d78",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "3dc2d791f77a9c719839705bf1548418096f2b7dbb6017f2b0cd9b5a64c42f25"
        ),
        CONFIG: "c86d15d1696b56d8ab5b15c2268efad5e03754148a6d9f8e84b95c3d6233fd44",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", EXCLUDE): {
        "gateseal.lock": (
            "7d1a1802f4ee6d06a4a7f013b9253b04727fb42295c3f4fa2c177d58581783a1"
        ),
        CONFIG: "3a88b2d4f19a4e2777264d6e781ab2046db54102087ad629ad7882d0f644c967",
        **PYTHON,
        **SECRETS,
    },
}


@pytest.mark.parametrize(
    "selection",
    list(GOLDEN),
    ids=[
        "hygiene",
        "python",
        "secrets",
        "secrets with betterleaks 1.8.0",
        "all",
        "all with betterleaks 1.8.0",
        "all with an exclude choice",
    ],
)
def test_bundled_selections_compile_to_the_reviewed_release_bytes(selection):
    """The Lock and every owned output of each bundled selection keep their bytes.

    The Lock records each output's digest, so its own digest covers them all;
    the outputs are named too, so a failure says which one changed.
    """
    policy = load_bundled_policy()
    source = BundledSource(policy.release)
    capabilities = tuple(item for item in selection if not item.startswith("\n"))
    extra = "".join(item for item in selection if item.startswith("\n"))
    manifest = render(capabilities, source) + extra.encode()
    chosen = resolve(parse_manifest(manifest), policy)
    lock = compile_lock(manifest, source, policy, PIN, chosen)
    digests = {name: entry["sha256"] for name, entry in lock["outputs"].items()}
    digests["gateseal.lock"] = hashlib.sha256(canonical(lock)).hexdigest()
    assert digests == GOLDEN[selection]


def definition(name):
    return files("gateseal").joinpath("data", "tools", f"{name}.tool.toml").read_text()


def refused(name, old, new):
    text = definition(name)
    assert old in text
    with pytest.raises(GatesealError) as failure:
        load_definition(name, text.replace(old, new, 1).encode(), Origin.BUILT_IN)
    return failure.value


def test_built_in_tools_name_only_owned_files():
    ruff, betterleaks = built_in_tools()["ruff"], built_in_tools()["betterleaks"]
    assert ruff.hooks["ruff-format"].args == ("--config", RUFF, "--")
    assert ruff.hooks["ruff-check"].probe[:4] == ("ruff", "check", "--config", RUFF)
    assert betterleaks.owned == (RULES, REPORT, HISTORY_REPORT)
    args = betterleaks.hooks["betterleaks"].args
    assert args[args.index("--report-template") + 1] == REPORT
    assert betterleaks.hooks["betterleaks"].probe[-2:] == ("--config", RULES)


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("ruff", '"pre-commit-repository"', '"project-environment"\nlock = "uv.lock"'),
        ("ruff", '["commit", "run", "ci"]', '["commit", "run", "ci", "push"]'),
        ("ruff", '["commit", "run", "ci"]', '["commit", "run"]'),
        ("ruff", 'scope = "files"', 'scope = "project"\nlock = "uv.lock"'),
        ("ruff", 'output = "plain"', 'output = "sarif"'),
        ("ruff", 'format = "toml"', 'format = "yaml"'),
        ("betterleaks", 'linux-x64 = "linux_x64"', 'windows-x64 = "windows_x64"'),
    ],
    ids=["kind", "stage", "fewer stages", "scope", "output", "format", "platform"],
)
def test_values_this_compiler_lacks_need_a_newer_compiler(name, old, new):
    failure = refused(name, old, new)
    assert failure.code == "unsupported_tool_definition"
    assert failure.next_step == (
        "Use a gateseal compiler that implements this tool definition."
    )


@pytest.mark.parametrize(
    "name,old,new",
    [
        (
            "ruff",
            '"--config", "{configuration}", "--fix"',
            '"--config={configuration}"',
        ),
        ("ruff", '"--config", "{configuration}", "--fix"', '"--config", "{report}"'),
        ("ruff", '"--config", "{configuration}", "--fix"', '"--config", "{config"'),
        ("ruff", '[configuration]\nformat = "toml"\n', ""),
        ("ruff", '  "ruff", "check"', '  "/usr/bin/ruff", "check"'),
        ("ruff", 'name = "ruff"', 'name = "../ruff"'),
        ("ruff", '["RUFF_"]', '["ruff"]'),
        ("ruff", '"ruff {version}"', '"ruff"'),
        ("ruff", '"ruff {version}"', '"ruff ({version}"'),
        ("ruff", '"ruff {version}"', '"ruff (?:{version})?.*"'),
        ("ruff", '"ruff {version}"', '"(?:ruff {version})?.*"'),
        ("ruff", '"ruff {version}"', '"ruff {version}?.*"'),
        ("ruff", 'scope = "files"', 'scope = "files"\nentry = "ruff check"'),
        ("ruff", '"https://github.com/astral', '"http://github.com/astral'),
        ("ruff", 'tag = "v{version}"', 'tag = "v0.17.1"'),
        ("ruff", 'name = "ruff format"', 'name = "ruff check"'),
        ("betterleaks", 'probe = ["betterleaks"', 'probe = ["sh"'),
        (
            "betterleaks",
            '[executable]\nname = "betterleaks"\nversion = ["version"]\n'
            'version_output = "{version}"\n',
            "",
        ),
        ("betterleaks", "_{version}_{platform}.tar.gz", "_{version}.tar.gz"),
        ("betterleaks", '"betterleaks_{version}', '"../{version}'),
        ("betterleaks", "extension = ", 'extension = "tmpl"\npath = "/etc/x"\n#'),
    ],
    ids=[
        "joined placeholder",
        "unknown placeholder",
        "stray brace",
        "configuration placeholder without configuration",
        "probe path",
        "executable path",
        "prefix",
        "version output without version",
        "version output pattern",
        "optional version",
        "version inside an optional group",
        "repeated version",
        "command field",
        "http repository",
        "tag without version",
        "hook name twice",
        "probe of another program",
        "release binary without executable",
        "archive without platform",
        "archive path",
        "file path",
    ],
)
def test_definitions_cannot_name_programs_or_files_gateseal_does_not_own(
    name, old, new
):
    assert refused(name, old, new).code == "invalid_field"


SINCE = "[choices.target_version.since]\n"
OTHER_CHOICE = '[choices.other]\nkey = "target-version"\nsince = { py39 = "0.1.0" }\n\n'


@pytest.mark.parametrize(
    "name,old,new",
    [
        (
            "ruff",
            "[choices.target_version]",
            "[choices.Target]\n[choices.target_version]",
        ),
        ("ruff", 'key = "target-version"', 'key = "target_version"'),
        ("ruff", 'key = "target-version"', 'key = "target-version"\ndefault = "py39"'),
        ("ruff", 'py39 = "0.1.0"', 'py39 = "0.1"'),
        ("ruff", 'py39 = "0.1.0"', '"py3.9" = "0.1.0"'),
        ("ruff", 'py39 = "0.1.0"', 'py39 = ["0.1.0"]'),
        ("ruff", "[choices.target_version]", OTHER_CHOICE + "[choices.target_version]"),
        (
            "pre-commit-hooks",
            "[source]",
            '[choices.mode]\nkey = "mode"\nsince = { fast = "1.0.0" }\n\n[source]',
        ),
    ],
    ids=[
        "name",
        "key",
        "extra field",
        "unstable release",
        "value",
        "release list",
        "key twice",
        "without configuration",
    ],
)
def test_definition_choices_are_validated(name, old, new):
    assert refused(name, old, new).code == "invalid_field"


def test_a_definition_choice_needs_an_accepted_value():
    text = re.sub(r'(?m)^py[0-9]+ = "[0-9.]+"\n', "", definition("ruff"))
    assert SINCE + "\n" in text
    with pytest.raises(GatesealError) as failure:
        load_definition("ruff", text.encode(), Origin.BUILT_IN)
    assert failure.value.code == "invalid_field"


@pytest.mark.parametrize(
    "output,reported",
    [
        ("ruff {version}", "ruff 1.9.0"),
        ("{version}", "1.9.0"),
        ("mutmut, version {version}", "mutmut, version 1.9.0"),
        ("mypy {version} .*", "mypy 1.9.0 (compiled: yes)"),
    ],
)
def test_version_proof_accepts_the_selected_version(output, reported):
    Executable("tool", ("--version",), output).require_version(reported, "1.9.0", "x")


@pytest.mark.parametrize("reported", ["11.9.0", "1.9.0.1", "1.9.0-rc1", "1.9.1"])
def test_version_proof_cannot_be_loosened(reported):
    with pytest.raises(GatesealError) as failure:
        Executable("tool", ("--version",), ".*{version}.*").require_version(
            reported, "1.9.0", "tool"
        )
    assert failure.value.code == "tool_failed"
    assert str(failure.value) == f"Prepared tool reports version {reported}, not 1.9.0."


@pytest.mark.parametrize("output", ["{version}|.*", ".*|{version}"])
def test_alternation_cannot_skip_the_version(output):
    with pytest.raises(GatesealError) as failure:
        Executable("tool", ("--version",), output).require_version(
            "9.9.9", "1.9.0", "x"
        )
    assert failure.value.code == "tool_failed"


def test_processes_start_without_any_built_in_tool_settings(monkeypatch):
    for key in ["RUFF_CONFIG", "BETTERLEAKS_CONFIG", "GITLEAKS_CONFIG", "RUFFLE"]:
        monkeypatch.setenv(key, "x")
    environment = tool_environment()
    assert "RUFFLE" in environment
    assert not {"RUFF_CONFIG", "BETTERLEAKS_CONFIG", "GITLEAKS_CONFIG"} & set(
        environment
    )
