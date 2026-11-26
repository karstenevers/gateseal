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
from gateseal.tools.model import Origin, Stage
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
IGNORE = ".gateseal/prettier-ignore.txt"
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
    ESLINT: "c642ecb8698ae76341f650da43c99674e90d63db6e13373eed0cc32419f33e8b",
    LOADER: "416aa4d02ff72014ad63315e9b7ff4604872169da0ed2a7c8d1e44aea70a50aa",
    PRETTIER: "8337e1afa204789e8ba2ee47068064308807817324f17f290ccd57d9be88109f",
    IGNORE: "28735de13e2a37c692c0ea43d0d241284612bc298da349e57964786e36b7d9b1",
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
            "aa72a95c4eef9c304cea61b323a5d800cb7d5aa092796b27918c14b22d311d7a"
        ),
        CONFIG: "8c5cad35ae3a2f3e9090967cbb3bf10153977d424e757041c4a4b341cf7b037b",
    },
    ("hygiene", CI): {
        "gateseal.lock": (
            "5386361f99f6be787ab30b95284e70e9678d947278900d39e909b539f2beeb4f"
        ),
        CONFIG: "8c5cad35ae3a2f3e9090967cbb3bf10153977d424e757041c4a4b341cf7b037b",
        WORKFLOW: "b3496ba1e7b787ed736e2ee864055d54dfeb5d5a699c9ba6415a2f6b49360cf9",
    },
    ("hygiene", "python"): {
        "gateseal.lock": (
            "dad68bb8403e07892d6fbea456f961855fa1376758a9c3083225f8e4913f4954"
        ),
        CONFIG: "236167f59d52dffe1396b17fcd41d294855e7f02d2b642ec8302d49ba761eaa7",
        **PYTHON,
    },
    ("hygiene", "secrets"): {
        "gateseal.lock": (
            "6de61cc673359c0c5fec861103d70592df162a6dcfef385b997d67f4219c98f9"
        ),
        CONFIG: "37382b79b644e6eddcc574268eea858d24d3aedf841071c4d200eff8a779e813",
        **SECRETS,
    },
    ("hygiene", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "5098b27ec8600b733dd4fd3d5709f82ad14df02455583101d521bcaaff83e5e7"
        ),
        CONFIG: "11961c1b20fc0c5bd290e7d76bf99f3e3e048a7e7facc671fc7ab9e683d90251",
        **SECRETS,
    },
    ("hygiene", "python", "secrets"): {
        "gateseal.lock": (
            "8a9060405fea7573094bd096ea6553e9f882c9a9f5f7dc418287a46c65b8fbe3"
        ),
        CONFIG: "e24f68c3866a967b197d4f863f58a90cdc986221bc26d4c080e359dac264b2a7",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", BETTERLEAKS_1_8): {
        "gateseal.lock": (
            "6daf9892cf6e6a55d3f38798760b7eb648fedf649e03727fcb11938d81c7ca67"
        ),
        CONFIG: "ac9c680e62a7426b1e4b3c2d74ea94190542465bfb97582b4dbff55e182f9427",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "secrets", EXCLUDE): {
        "gateseal.lock": (
            "e95df1c7375f6e980b8560d58fe5f61a27045f23703ea8cb83e8ea35f9a669db"
        ),
        CONFIG: "6707e55f53944a8c929a28ef17e7c13fe28d6e414f735c9714b4c20b4ee51fc4",
        **PYTHON,
        **SECRETS,
    },
    ("hygiene", "python", "python-types"): {
        "gateseal.lock": (
            "f3a5d8b8886ed6cc4c0d8ef81ce7afd7960a7bd0907ed31c9105bd5c48482ff9"
        ),
        CONFIG: "4cf07b2f0a246da78625c58be61c1f1557590ae4ce31e8a746a1a4cb611571c1",
        PYRIGHT: "e54cf168d05c0ce31ddcfb8ce904805d82e00a49db243f98ba7e23de2d312684",
        **PYTHON,
    },
    ("hygiene", "typescript"): {
        "gateseal.lock": (
            "c2a6ca58e19e27bdda630404bdd0dea14f3dd192439ce43cab7c95fdfc53f6ec"
        ),
        CONFIG: "b96d91315461442585f300d9272d6317dbaf0bdff8b2f9ba4ddc5b218d340a6c",
        **TYPESCRIPT,
    },
    ("hygiene", "typescript", "typescript-types"): {
        "gateseal.lock": (
            "2b9d0ff79873c09a5e193b100ad0758ae78cd34fe589668d2e10f005d47540d2"
        ),
        CONFIG: "45e7fd010427200b9b51552cb144cb75b6f78b4e8de4182f854b5d51d5b7f840",
        ".gateseal/tsc.package-lock.json": (
            "454a05c1b8202c11775d92f24048111003d4cc731e1e5d03326b67799ac76051"
        ),
        **TYPESCRIPT,
    },
    (
        "hygiene",
        "typescript",
        "typescript-types",
        "typescript-audit",
        "typescript-unused",
    ): {
        "gateseal.lock": (
            "abd5a8558f98115c03e259cfc30195dc3628d050307ca30ad8120deda4215282"
        ),
        CONFIG: "b6e9536f0b68aad704cf8595795cab17bedd7ee78b0ad90ccbd71550c44410e0",
        ".gateseal/tsc.package-lock.json": (
            "454a05c1b8202c11775d92f24048111003d4cc731e1e5d03326b67799ac76051"
        ),
        ".gateseal/knip.toml": (
            "1532542f0473c9389fedad794667a0bcdf26d374902a05bba6bfd571929d79b7"
        ),
        ".gateseal/knip-loader.mjs": (
            "5a62b046ff3d09daaec693b7720f42384f031195cb29da70189a605bd1367226"
        ),
        ".gateseal/knip.package-lock.json": (
            "8ca59a3eef32ee898e489925c68b9523977414bd43118fef4d093b9f9ed06800"
        ),
        ".gateseal/osv-scanner.toml": (
            "e729d669b14adac54a1d9c01264b344b0330a730252658cf6dbeb0d73f4c18df"
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
        "typescript with every check",
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


def bundled_before(root, capability):
    """Return the bundled policy as it was before it offered capability.

    Every capability after it in policy.toml goes too, with its tools' records.
    """
    for asset in load_bundled_policy().assets:
        (root / asset.path).parent.mkdir(parents=True, exist_ok=True)
        (root / asset.path).write_bytes(asset.content)
    path = root / "policy.toml"
    kept, dropped = path.read_text().split(f"\n# {capability}")
    path.write_text(kept)
    locked = json.loads((root / "policy.lock").read_bytes())
    table = re.compile(r"^\[capabilities\.[a-z-]+\.tools\.([a-z-]+)\]$", re.M)
    for tool in table.findall(dropped):
        del locked["components"][tool]
    (root / "policy.lock").write_bytes(canonical(locked))
    return load_policy_tree(root)


@pytest.mark.parametrize(
    ("added", "selection"),
    [
        ("typescript-types", ("hygiene", "typescript")),
        ("typescript-types", ("hygiene", "python")),
        ("typescript-audit", ("hygiene", "typescript", "typescript-types")),
    ],
    ids=["typescript", "python", "typescript with types"],
)
def test_a_selection_without_a_new_capability_changes_only_in_the_policy_digest(
    tmp_path, added, selection
):
    def compiled(policy):
        source = BundledSource(policy.release)
        manifest = render(selection, source)
        chosen = resolve(parse_manifest(manifest), policy)
        lock = compile_lock(manifest, source, policy, PIN, chosen)
        return lock, compile_outputs(chosen, PIN, source)

    bundled, earlier = load_bundled_policy(), bundled_before(tmp_path, added)
    assert added not in earlier.capabilities
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
    assert prettier.owned == (PRETTIER, IGNORE)
    assert prettier.files[IGNORE].startswith(b"# ")
    assert prettier.files[IGNORE].count(b"\n") == 1
    assert prettier.hooks["prettier"].args[:2] == ("--config", PRETTIER)
    assert "--ignore-path" in prettier.hooks["prettier"].args
    assert IGNORE in prettier.hooks["prettier"].args
    assert prettier.hooks["prettier"].args[-1] == "--"
    assert eslint.files[LOADER].startswith(
        b"// gateseal owns this file; gateseal configure writes it.\n"
    )


def test_the_dependency_checks_read_only_the_policy_s_settings():
    knip, audit = built_in_tools()["knip"], built_in_tools()["osv-scanner"]
    assert knip.owned == (".gateseal/knip.toml", ".gateseal/knip-loader.mjs")
    assert knip.hooks["knip"].args[:2] == ("--config", ".gateseal/knip-loader.mjs")
    assert audit.owned == (".gateseal/osv-scanner.toml",)
    args = audit.hooks["osv-scanner"].args
    assert args[args.index("--config") + 1] == ".gateseal/osv-scanner.toml"
    assert audit.stages == frozenset({Stage.CI})


def allowed(program):
    return {program[i + 1] for i, word in enumerate(program) if word == "--allow-env"}


def test_only_a_tool_that_asks_a_service_gets_the_proxy_in_each_start():
    """A hook and a history scan, which names its own variables, start it alike."""
    tools = load_bundled_policy().tools
    network = {"HTTPS_PROXY", "https_proxy", "NO_PROXY", "SSL_CERT_FILE"}
    audit, scan = tools["osv-scanner"], ("GIT_NO_LAZY_FETCH",)
    assert network <= allowed(audit.program())
    assert network | set(scan) <= allowed(audit.program(scan))
    assert allowed(tools["betterleaks"].program(scan)) == set(scan)


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
        ("osv-scanner", "network = true", 'network = "yes"'),
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
        "network that is no boolean",
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
