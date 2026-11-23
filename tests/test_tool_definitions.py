import hashlib
import json
import re
from importlib.resources import files

import pytest
from conftest import PIN
from gateseal.diagnostics import GatesealError
from gateseal.lock import compile_lock, compile_outputs
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import load_bundled_policy, load_policy_tree
from gateseal.tools import built_in_tools, load_definition
from gateseal.tools.model import Origin
from gateseal.validation import canonical

BETTERLEAKS_1_8 = (
    '\n[exceptions.betterleaks]\nversion = "1.8.0"\nreason = "r"\n'
    "review_by = 2026-12-31\n"
)
EXCLUDE = '\n[choices.hygiene]\nexclude = ["vendor", "docs/gen.d"]\n'
CI = '\n[ci]\nprovider = "github"\ndefault_branch = "main"\n'
WORKFLOW = ".github/workflows/gateseal.yml"
CONFIG = ".pre-commit-config.yaml"
RUFF = ".gateseal/ruff.toml"
PYRIGHT = ".gateseal/pyright.toml"
RULES = ".gateseal/betterleaks.toml"
ESLINT = ".gateseal/eslint.json"
LOADER = ".gateseal/eslint-loader.mjs"
SUPPRESSIONS = ".gateseal/eslint-suppressions.json"
PRETTIER = ".gateseal/prettier.toml"
REPORT = ".gateseal/betterleaks-report.tmpl"
HISTORY_REPORT = ".gateseal/betterleaks-history-report.tmpl"
SECRETS = {
    RULES: "af60be81d22bdd646aa8b41ba8955d86420d71b486a14f81fa1d971293e4c71d",
    REPORT: "e90202b5add329ed4cc8e507dacdda7bbdc9cb72b37aa717a8f31897bf53cdf9",
    HISTORY_REPORT: (
        "6a78914b3697c6e2a5baa53330b760938a191b367afc428947eb9ca4465c6aa6"
    ),
}
PYTHON = {RUFF: "ffcf7d3c282f374519e8110da535fbde8e4d9dd0d81b7dad32db64a6979f8c2c"}
ALL = "6edbc51f7109342deb3f7caf0fca05e49f7dbf1c70171fddfe6c2ebbec439191"
TYPESCRIPT = {
    ESLINT: "cd501c706e77af1e4de829bd1a133f76eb3644bb423cf8a469ef1845c2a77071",
    LOADER: "310cc794928ef7a784f334f25523c71a68cba4ae2fd7796125c0c9f80ec01c0c",
    PRETTIER: "8337e1afa204789e8ba2ee47068064308807817324f17f290ccd57d9be88109f",
    SUPPRESSIONS: "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356",
    ".gateseal/eslint.package-lock.json": (
        "a320bd4523541011470886447ac051be8396763bd8a34717bf7abd95ea076145"
    ),
    ".gateseal/prettier.package-lock.json": (
        "063f3437b59d4aa948ca24043db027ec3c6b1313477111aee4ef2c9bb4ebff3c"
    ),
}

GOLDEN = {
    ("hygiene",): {
        "gateseal.lock": (
            "82b9bfa46acf8f65e9f6c4bb50b20f9bad9df034aae6be66636248cb67280bc2"
        ),
        CONFIG: "8c5cad35ae3a2f3e9090967cbb3bf10153977d424e757041c4a4b341cf7b037b",
    },
    ("hygiene", CI): {
        "gateseal.lock": (
            "5a2948d560cfef16cac085ed991b5c6c2372a7abdba0c6ea5137cdb12e2c71f4"
        ),
        CONFIG: "8c5cad35ae3a2f3e9090967cbb3bf10153977d424e757041c4a4b341cf7b037b",
        WORKFLOW: "a20efbe6dac974e16bb02ac5a358ab1a3926116daa3520b560505b032010c6ca",
    },
    ("hygiene", "python"): {
        "gateseal.lock": (
            "6cdf4bc8cbb651ced061ef31245b1dfe2dac4c63ab1ef3ed64a8c321cd066362"
        ),
        CONFIG: "236167f59d52dffe1396b17fcd41d294855e7f02d2b642ec8302d49ba761eaa7",
        **PYTHON,
    },
    ("hygiene", "secrets"): {
        "gateseal.lock": (
            "c3290625626272b1a6082b7ae6aa3482c06577679916ed120b2e2600b939eec2"
        ),
        CONFIG: "37382b79b644e6eddcc574268eea858d24d3aedf841071c4d200eff8a779e813",
        **SECRETS,
    },
    ("hygiene", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "40acc0092033ef92fc7938b7eca6e306bccf107024f1995564088973bc1ed271"
        ),
        CONFIG: "11961c1b20fc0c5bd290e7d76bf99f3e3e048a7e7facc671fc7ab9e683d90251",
        **SECRETS,
    },
    ("hygiene", "python", "secrets"): {
        "gateseal.lock": (
            "d8bf690b764aafe7366136ffc146af050c421b7ba1ff24f3c58eb5adb2c80d66"
        ),
        CONFIG: "e24f68c3866a967b197d4f863f58a90cdc986221bc26d4c080e359dac264b2a7",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "a154e4dea19f12e36a285c5fb464024e5b41454a0fcc805ec9e0191c99b24288"
        ),
        CONFIG: "ac9c680e62a7426b1e4b3c2d74ea94190542465bfb97582b4dbff55e182f9427",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", EXCLUDE): {
        "gateseal.lock": (
            "e724ae39469ff488392f8049db32e70013f9291863dc33e51f24c39422e5b315"
        ),
        CONFIG: "6707e55f53944a8c929a28ef17e7c13fe28d6e414f735c9714b4c20b4ee51fc4",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "python-types"): {
        "gateseal.lock": (
            "604ba94906ff73fdfec690f24dd8752f15bb41557415384949666476def4329e"
        ),
        CONFIG: "4cf07b2f0a246da78625c58be61c1f1557590ae4ce31e8a746a1a4cb611571c1",
        PYRIGHT: "e54cf168d05c0ce31ddcfb8ce904805d82e00a49db243f98ba7e23de2d312684",
        **PYTHON,
    },
    ("hygiene", "typescript"): {
        "gateseal.lock": (
            "4436fdabff926185ccd4ce35f295e0b715ac9fc47beca28751d4c296187bfdc5"
        ),
        CONFIG: "eaaf86891a6bdf161a5d2897fcd259612150df3a84b9af75b0f62ef19aaf5ae8",
        **TYPESCRIPT,
    },
    ("hygiene", "typescript", "typescript-types"): {
        "gateseal.lock": (
            "7d37575ec8ff8f9e0dba33a3ec5fea51062ea5addc70ec3e298c10b9e173169e"
        ),
        CONFIG: "8bd4d001a4c27a116c9290927cb818eb43779cadeb6de7954efaea12b954d01e",
        ".gateseal/tsc.package-lock.json": (
            "454a05c1b8202c11775d92f24048111003d4cc731e1e5d03326b67799ac76051"
        ),
        **TYPESCRIPT,
    },
}


@pytest.mark.parametrize(
    "selection",
    list(GOLDEN),
    ids=[
        "hygiene",
        "hygiene with a github workflow",
        "python",
        "secrets",
        "secrets with betterleaks 1.8.0",
        "all",
        "all with betterleaks 1.8.0",
        "all with an exclude choice",
        "python with types",
        "typescript",
        "typescript with types",
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


def without_typescript_types(root):
    """Return the bundled policy as it was before it offered typescript-types."""
    for asset in load_bundled_policy().assets:
        (root / asset.path).parent.mkdir(exist_ok=True)
        (root / asset.path).write_bytes(asset.content)
    path = root / "policy.toml"
    path.write_text(path.read_text().split("\n# typescript-types")[0])
    locked = json.loads((root / "policy.lock").read_bytes())
    del locked["components"]["tsc"]
    (root / "policy.lock").write_bytes(canonical(locked))
    return load_policy_tree(root)


@pytest.mark.parametrize("capability", ["typescript", "python"])
def test_a_selection_without_typescript_types_changes_only_in_the_policy_digest(
    tmp_path, capability
):
    def compiled(policy):
        source = BundledSource(policy.release)
        manifest = render(("hygiene", capability), source)
        chosen = resolve(parse_manifest(manifest), policy)
        lock = compile_lock(manifest, source, policy, PIN, chosen)
        return lock, compile_outputs(chosen, PIN, source)

    bundled, earlier = load_bundled_policy(), without_typescript_types(tmp_path)
    assert "typescript-types" not in earlier.capabilities
    (lock, outputs), (earlier_lock, earlier_outputs) = map(compiled, (bundled, earlier))
    assert outputs == earlier_outputs
    assert lock["policy"] == earlier_lock["policy"] | {"digest": bundled.content_digest}
    assert bundled.content_digest != earlier.content_digest
    fingerprint = ("policy", "inputs_fingerprint")
    assert {k: v for k, v in lock.items() if k not in fingerprint} == {
        k: v for k, v in earlier_lock.items() if k not in fingerprint
    }


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
    assert betterleaks.owned == (RULES, REPORT, HISTORY_REPORT)
    args = betterleaks.hooks["betterleaks"].args
    assert args[args.index("--report-template") + 1] == REPORT


def test_built_in_npm_tools_name_only_owned_files():
    eslint, prettier = built_in_tools()["eslint"], built_in_tools()["prettier"]
    assert eslint.owned == (ESLINT, LOADER, SUPPRESSIONS)
    assert eslint.files[SUPPRESSIONS] == b"{}\n"
    assert eslint.hooks["eslint"].args == (
        "--config",
        LOADER,
        "--suppressions-location",
        SUPPRESSIONS,
        "--no-warn-ignored",
        "--fix",
        "--",
    )
    assert prettier.owned == (PRETTIER,)
    assert prettier.hooks["prettier"].args[:2] == ("--config", PRETTIER)
    assert prettier.hooks["prettier"].args[-1] == "--"
    assert eslint.files[LOADER].startswith(
        b"// gateseal owns this file; gateseal configure writes it.\n"
    )


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("ruff", '"pre-commit-repository"', '"project-environment"\nlock = "uv.lock"'),
        ("ruff", '["commit", "run", "ci"]', '["commit", "run", "ci", "push"]'),
        ("ruff", '["commit", "run", "ci"]', '["commit", "run"]'),
        ("ruff", 'scope = "files"', 'scope = "workspace"'),
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
        ("betterleaks", 'name = "betterleaks"', 'name = "../betterleaks"'),
        ("ruff", 'scope = "files"', 'scope = "files"\nentry = "ruff check"'),
        ("ruff", '"https://github.com/astral', '"http://github.com/astral'),
        ("ruff", 'tag = "v{version}"', 'tag = "v0.17.1"'),
        ("ruff", 'name = "ruff format"', 'name = "ruff check"'),
        (
            "betterleaks",
            '[executable]\nname = "betterleaks"\ncheck_at_commit = true\n',
            "",
        ),
        ("betterleaks", "check_at_commit = true", 'check_at_commit = "yes"'),
        ("ruff", 'scope = "files"', 'scope = "files"\n[executable]\nname = "ruff"'),
        ("betterleaks", "_{version}_{platform}.tar.gz", "_{version}.tar.gz"),
        ("betterleaks", '"betterleaks_{version}', '"../{version}'),
        ("betterleaks", "extension = ", 'extension = "tmpl"\npath = "/etc/x"\n#'),
    ],
    ids=[
        "joined placeholder",
        "unknown placeholder",
        "stray brace",
        "configuration placeholder without configuration",
        "executable path",
        "command field",
        "http repository",
        "tag without version",
        "hook name twice",
        "release binary without executable",
        "check at commit that is no boolean",
        "pre-commit repository with an executable",
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
