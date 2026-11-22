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
    LOADER: "3e956e1c95547b5c15fc6ceec14d0624ceace0d88b238906f1b283ad4b1ef217",
    PRETTIER: "8337e1afa204789e8ba2ee47068064308807817324f17f290ccd57d9be88109f",
    SUPPRESSIONS: "ca3d163bab055381827226140568f3bef7eaac187cebd76878e0b63e9e442356",
}

GOLDEN = {
    ("hygiene",): {
        "gateseal.lock": (
            "36a0b97315592b4983e979b8eeae6a929012b0843c6d4fad71c9383a4ba8496d"
        ),
        CONFIG: "8c5cad35ae3a2f3e9090967cbb3bf10153977d424e757041c4a4b341cf7b037b",
    },
    ("hygiene", CI): {
        "gateseal.lock": (
            "76267b8e014091bc351d776a29a3c900f2f8b688aab275cd9d144e7ee5f4d785"
        ),
        CONFIG: "8c5cad35ae3a2f3e9090967cbb3bf10153977d424e757041c4a4b341cf7b037b",
        WORKFLOW: "a20efbe6dac974e16bb02ac5a358ab1a3926116daa3520b560505b032010c6ca",
    },
    ("hygiene", "python"): {
        "gateseal.lock": (
            "eae882c41331f15f7f62d0dcba780339c8655513d0724a21fc1c79defed60885"
        ),
        CONFIG: "236167f59d52dffe1396b17fcd41d294855e7f02d2b642ec8302d49ba761eaa7",
        **PYTHON,
    },
    ("hygiene", "secrets"): {
        "gateseal.lock": (
            "cf03838d6052bd861cc1c5d3c1e2c36a5d8843ebd4f792ae6d4e5b680cd8dfa4"
        ),
        CONFIG: "37382b79b644e6eddcc574268eea858d24d3aedf841071c4d200eff8a779e813",
        **SECRETS,
    },
    ("hygiene", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "18d9757dc9ed2dddfd7a0baf45641149752595ce85edd1e799c9f3bd1e50750b"
        ),
        CONFIG: "11961c1b20fc0c5bd290e7d76bf99f3e3e048a7e7facc671fc7ab9e683d90251",
        **SECRETS,
    },
    ("hygiene", "python", "secrets"): {
        "gateseal.lock": (
            "eae811e889c8e0d6ad4408626dd7d1e37134cf41173b1054c8c6b0a4ef85c367"
        ),
        CONFIG: "e24f68c3866a967b197d4f863f58a90cdc986221bc26d4c080e359dac264b2a7",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "e7f87d82efe8eeb283d1cd6597f50cca1a2025bbbabd67c3283d2eacfb258526"
        ),
        CONFIG: "ac9c680e62a7426b1e4b3c2d74ea94190542465bfb97582b4dbff55e182f9427",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", EXCLUDE): {
        "gateseal.lock": (
            "b73747b6a25f2241e0ae1a156fd2be74d3e7e9c46fd86b5b5d290b6cd103f2aa"
        ),
        CONFIG: "6707e55f53944a8c929a28ef17e7c13fe28d6e414f735c9714b4c20b4ee51fc4",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "python-types"): {
        "gateseal.lock": (
            "6f616881dcb60781d954942dda056e4434dbb26b1292c338b59671966b453deb"
        ),
        CONFIG: "d027f65dbbee8a8cc0466becddb3e526f84aab9d15df91fcb36e9cd2e2ead797",
        PYRIGHT: "e7b7d2f1eb0b2fc23e760f8dc124c5a4cedb48a02e97ef94d17537c85f00faf7",
        **PYTHON,
    },
    ("hygiene", "typescript"): {
        "gateseal.lock": (
            "d09fed776b3a841dcba07ffcd91522eab33d18b18629b2437fcafef76fb64360"
        ),
        CONFIG: "5a4243fbb9a076ee9821ef1f7623fa7b4a98e2ce67fccbcce9e31a1665931bcf",
        **TYPESCRIPT,
    },
    ("hygiene", "typescript", "typescript-types"): {
        "gateseal.lock": (
            "fc3bb55bf7aee162a3dd942315181320aa81100be29e9d6bc7d4bb4366309485"
        ),
        CONFIG: "bf720f44931a16b7a3b6f97d383cd1ac1a50574f52923ad9d9155d6581f661f6",
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
