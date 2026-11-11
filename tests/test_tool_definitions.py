import hashlib
import re
from importlib.resources import files

import pytest
from gateseal.compiler_release import CompilerArtifact
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
MISE = ".gateseal/mise.toml"
SECRETS = {
    RULES: "183ff997bc28f117c430b0b47903b9ddc92f697e8d421dc126e191fdc583c26e",
    REPORT: "e90202b5add329ed4cc8e507dacdda7bbdc9cb72b37aa717a8f31897bf53cdf9",
    HISTORY_REPORT: (
        "6a78914b3697c6e2a5baa53330b760938a191b367afc428947eb9ca4465c6aa6"
    ),
    MISE: "13ebf309b67d09cdd07bbf992c25ebc6de299e5ccfc3667dc3e7b43d3bcf881f",
}
SECRETS_1_8 = {
    **SECRETS,
    MISE: "9961b0e2c557e9d96c55061a8cfa614298d37a2d3e71ddc5e3a42a415d707954",
}
PYTHON = {RUFF: "ffcf7d3c282f374519e8110da535fbde8e4d9dd0d81b7dad32db64a6979f8c2c"}
ALL = "d606081ed4765af042fbf593dc1ef9b80304e8e149f706238cb2a983b5915f85"

GOLDEN = {
    ("hygiene",): {
        "gateseal.lock": (
            "7eb888b2709ebe52be7c23b5412fd5f3c38f61f4ccf0eb8a1712c3d9851eee7a"
        ),
        CONFIG: "842259ac89e0fee4ef0d6ee5937b000111eafd8755a097bad7c7f6a2b82de9ad",
    },
    ("hygiene", "python"): {
        "gateseal.lock": (
            "6bf4ecf1db2d858bbc4a80b10816fd8ac83b6035b31aeae5372f27a841e6ab97"
        ),
        CONFIG: "47c023313666cec98b4418a3d7bd24a691abfb45faf18530c4e9bff6ddc19f6d",
        **PYTHON,
    },
    ("hygiene", "secrets"): {
        "gateseal.lock": (
            "0b574ad4336c2d975f1212fa9876d10a46670d8dca3bca88316a85446549122f"
        ),
        CONFIG: "ae6fb71109b0e8c8197e6d0c8b3f5d15dca9a374d5ecc8a6b70e16bdb130c9b1",
        **SECRETS,
    },
    ("hygiene", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "df75fdc8ff7e8549ca1a14440a98de75a075191dbea7d8a24361d660ab7fa74a"
        ),
        CONFIG: "ae6fb71109b0e8c8197e6d0c8b3f5d15dca9a374d5ecc8a6b70e16bdb130c9b1",
        **SECRETS_1_8,
    },
    ("hygiene", "python", "secrets"): {
        "gateseal.lock": (
            "a482ad2ee2f52d99c7b977bd4c3c0b71384248376ce6fa9e583c738286d4c2e2"
        ),
        CONFIG: ALL,
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "c1cc5dbcdea69532e3a821b9ee6c05a00366b9880ebb0b733502d21dc9260e52"
        ),
        CONFIG: ALL,
        **PYTHON,
        **SECRETS_1_8,
    },
    ("hygiene", "python", "secrets", EXCLUDE): {
        "gateseal.lock": (
            "0404ee29b34a9ffecdef2e38c818fb84be2e23d66b504e8584d2449a72401547"
        ),
        CONFIG: "e2d4b0aee6c89cb2c08442aee3ae29b80d75a598e8c0d34ead20447a46c6350b",
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
    wheel = "gateseal-0.1.0-py3-none-any.whl"
    artifact = CompilerArtifact(
        "local-wheel", f"/wheels/{wheel}", wheel, "a" * 64, "0.1.0"
    )
    chosen = resolve(parse_manifest(manifest), policy)
    lock = compile_lock(manifest, source, policy, artifact, chosen)
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
