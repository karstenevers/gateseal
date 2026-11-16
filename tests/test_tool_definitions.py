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
            "2a3c6d18281dd9e213e24e7754e3e16bfe9a67e25bec738c7b050e4a6e1e5692"
        ),
        CONFIG: "6a9652a1c613afbc2b912048af64691ea3b92422e86aaee13a5f473c7ff85e0c",
    },
    ("hygiene", "python"): {
        "gateseal.lock": (
            "98545f99ac66f78007f113d199537167c8abbc558b6ea993fd310f0771050fe7"
        ),
        CONFIG: "70d2ac1f5f68053e1b1e80b5229ca74cf8987c7a777537203545f289bd590077",
        **PYTHON,
    },
    ("hygiene", "secrets"): {
        "gateseal.lock": (
            "4406d3a7caaa25c1cb12bf4c650490234b867e790312d6b775509adf6d453e90"
        ),
        CONFIG: "4740b6ae5b22495c6f31f06013ab24302b54cdbba43bc0112a9028b3daa4d442",
        **SECRETS,
    },
    ("hygiene", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "35a834fecb0d68a95c47302b10614e00acfcb80210df74781ce58549eba22804"
        ),
        CONFIG: "5dae2948e98d147581e439d03fb23ba3ed160033b04eb087177dba2da9db1b71",
        **SECRETS,
    },
    ("hygiene", "python", "secrets"): {
        "gateseal.lock": (
            "964bf20f363282a752b5b7bd102e9f39d52bf5ba9ccfe9fdf320dfa510aac11a"
        ),
        CONFIG: "9a222e283b4caf856991e0a4647ce20ef1eb5a6104f374f22f711088200e22e7",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "ed12791d918d70c88483f8da9a76473ab72e71c12371a65f992b850f668c8734"
        ),
        CONFIG: "350228ae37e787ff3947ebbbbf95bd2593178fdfdf66b0b047f75d55ae890408",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", EXCLUDE): {
        "gateseal.lock": (
            "fd705f26175ef19d3c3a32296467d49834906d6489474932b2aa826d6c406ab8"
        ),
        CONFIG: "9e7dcc3bd2fc325ed8165f9c8a826bfb64716be3fac8e45df3278c88fa6535a0",
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
