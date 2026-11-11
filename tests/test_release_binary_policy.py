"""What a policy's release binaries compile to, and what policy.lock records.

Each test copies the bundled policy and edits it as a team would: a second
release binary with a choice between two of its hooks, a stale policy.lock,
or a Hook Python. policy.lock is written as gateseal policy lock writes it, so
loading the policy needs no upstream.
"""

import hashlib
import json
import shutil

import pytest
from black_policy import HOOK_PYTHON, PYTHON
from gateseal.compiler_release import CompilerArtifact
from gateseal.diagnostics import GatesealError
from gateseal.lock import compile_lock, compile_outputs
from gateseal.manifest import BundledSource, parse_manifest, render, resolve
from gateseal.policy import bundled_root, load_policy_tree
from gateseal.tools import built_in_tools
from gateseal.validation import canonical

GITLEAKS = """\
kind = "release-binary"
stages = ["commit", "run", "ci"]
scope = "files"
removed_prefixes = ["GITLEAKS_"]

[source]
repository = "https://github.com/gitleaks/gitleaks"
tag = "v{version}"
archive = "gitleaks_{version}_{platform}.tar.gz"
checksums = "gitleaks_{version}_checksums.txt"

[source.platforms]
linux-x64 = "linux_x64"

[configuration]
format = "toml"

[executable]
name = "gitleaks"
version = ["version"]
version_output = "{version}"

[hooks.gitleaks]
name = "Detect secrets with Gitleaks"
args = ["dir", "--config", "{configuration}"]
probe = ["gitleaks", "version"]
output = "concise"

[hooks.gitleaks-deep]
name = "Detect encoded secrets with Gitleaks"
args = ["dir", "--config", "{configuration}", "--max-decode-depth", "3"]
probe = ["gitleaks", "version"]
output = "concise"
"""

GITLEAKS_URL = (
    "https://github.com/gitleaks/gitleaks/releases/download/v8.30.0/"
    "gitleaks_8.30.0_linux_x64.tar.gz"
)
GITLEAKS_SHA = "1" * 64
BETTERLEAKS = "https://github.com/betterleaks/betterleaks/releases/download/v1.9.0/"
OWNED = "# gateseal owns this file; gateseal configure writes it from gateseal.lock."


def bundled_archives():
    """Return the bundled policy.lock's Betterleaks 1.9.0 archives by platform."""
    lock = json.loads((bundled_root() / "policy.lock").read_bytes())
    return lock["components"]["betterleaks"]["versions"]["1.9.0"]["platforms"]


def copied(tmp_path):
    root = tmp_path / "policy"
    shutil.copytree(bundled_root(), root, ignore=shutil.ignore_patterns("*.md"))
    return root


def edit(path, *replacements):
    text = path.read_text()
    for old, new in replacements:
        assert old in text, old
        text = text.replace(old, new, 1)
    path.write_text(text)


def edit_lock(root, change):
    lock = json.loads((root / "policy.lock").read_bytes())
    change(lock["components"])
    (root / "policy.lock").write_bytes(canonical(lock))


def with_gitleaks(tmp_path):
    """Return the bundled policy whose secrets also runs a team Gitleaks.

    Its choice deep_scan picks one of Gitleaks's two hooks.
    """
    root = copied(tmp_path)
    (root / "tools").mkdir()
    (root / "tools/gitleaks.toml").write_text(GITLEAKS)
    (root / "rules/gitleaks.toml").write_text('title = "Team rules"\n')
    edit(
        root / "policy.toml",
        (
            'tools = ["betterleaks"]\n',
            'tools = ["betterleaks", "gitleaks"]\n'
            'alternatives.deep_scan = ["gitleaks", "gitleaks-deep"]\n',
        ),
    )
    with (root / "policy.toml").open("a") as policy:
        policy.write(
            '\n[tools.gitleaks]\nconfiguration = "rules/gitleaks.toml"\n'
            '\n[choices.secrets.deep_scan]\ndefault = "gitleaks"\n'
            '\n[components.gitleaks]\npin = "8.30.0"\n\n'
            '[components.gitleaks.versions."8.30.0"]\n'
        )
    archive = {"executable": "gitleaks", "sha256": GITLEAKS_SHA, "url": GITLEAKS_URL}
    edit_lock(
        root,
        lambda components: components.update(
            gitleaks={"versions": {"8.30.0": {"platforms": {"linux-x64": archive}}}}
        ),
    )
    return load_policy_tree(root)


def compiled(policy, capabilities, extra=""):
    """Return the Lock and the outputs that configure compiles for capabilities."""
    source = BundledSource(policy.release)
    manifest = render(capabilities, source) + extra.encode()
    wheel = "gateseal-0.1.0-py3-none-any.whl"
    artifact = CompilerArtifact("local-wheel", f"/{wheel}", wheel, "a" * 64, "0.1.0")
    selection = resolve(parse_manifest(manifest), policy)
    lock = compile_lock(manifest, source, policy, artifact, selection)
    return lock, compile_outputs(selection, artifact, source)


def platform_line(platform, url, sha):
    return f'{platform} = {{ url = "{url}", checksum = "sha256:{sha}" }}'


def test_mise_toml_declares_each_release_binary_by_name(tmp_path):
    _, outputs = compiled(with_gitleaks(tmp_path), ("hygiene", "secrets"))
    archives = bundled_archives()
    assert outputs[".gateseal/mise.toml"].decode().splitlines() == [
        OWNED,
        '[tools."http:betterleaks"]',
        'version = "1.9.0"',
        "",
        '[tools."http:betterleaks".platforms]',
        *(
            platform_line(platform, archive["url"], archive["sha256"])
            for platform, archive in sorted(archives.items())
        ),
        "",
        '[tools."http:gitleaks"]',
        'version = "8.30.0"',
        "",
        '[tools."http:gitleaks".platforms]',
        platform_line("linux-x64", GITLEAKS_URL, GITLEAKS_SHA),
    ]


@pytest.mark.parametrize(
    "chosen,arguments",
    [
        ("gitleaks", ""),
        ("gitleaks-deep", " --max-decode-depth 3"),
    ],
)
def test_a_choice_between_two_hooks_of_a_release_binary_runs_one(
    tmp_path, chosen, arguments
):
    choice = f'\n[choices.secrets]\ndeep_scan = "{chosen}"\n'
    lock, outputs = compiled(with_gitleaks(tmp_path), ("hygiene", "secrets"), choice)
    repositories = json.loads(outputs[".pre-commit-config.yaml"])["repos"]
    assert [
        (hook["id"], hook["entry"])
        for repository in repositories
        for hook in repository["hooks"]
        if hook["id"].startswith("gitleaks")
    ] == [
        (chosen, "gateseal-gitleaks dir --config .gateseal/gitleaks.toml" + arguments)
    ]
    assert lock["native_artifacts"]["gitleaks"]["version"] == "8.30.0"


@pytest.mark.parametrize(
    "kept,recorded",
    [((), "no platform"), (("linux-x64",), "linux-x64")],
    ids=["none", "one of two"],
)
def test_policy_lock_records_every_platform_the_definition_names(
    tmp_path, kept, recorded
):
    root = copied(tmp_path)

    def drop(components):
        platforms = components["betterleaks"]["versions"]["1.9.0"]["platforms"]
        for platform in set(platforms) - set(kept):
            del platforms[platform]

    edit_lock(root, drop)
    with pytest.raises(GatesealError) as failure:
        load_policy_tree(root)
    assert failure.value.code == "policy_lock_stale"
    message = str(failure.value)
    assert recorded in message
    # The recorded platforms, then the two the definition names.
    assert message.count("linux-x64") == len(kept) + 1
    assert message.count("macos-arm64") == 1
    assert "gateseal policy lock" in failure.value.next_step


def test_only_a_python_language_tool_runs_on_the_hook_python(tmp_path):
    """A hygiene of release binaries alone needs no Hook Python; python does."""
    root = copied(tmp_path)
    hooks = '["trailing-whitespace", "end-of-file-fixer", "check-yaml"]'
    edit(
        root / "policy.toml",
        ('[capabilities.hygiene]\ntools = ["pre-commit-hooks"]\n\n', ""),
        ("[capabilities.secrets]\n", "[capabilities.hygiene]\n"),
        (f"[tools.pre-commit-hooks]\nhook_ids = {hooks}\n\n", ""),
        ('[components.pre-commit-hooks]\npin = "6.0.0"\n\n', ""),
        ('[components.pre-commit-hooks.versions."6.0.0"]\n\n', ""),
        ('[components.pre-commit-hooks.versions."5.0.0"]\n\n', ""),
    )
    with (root / "policy.toml").open("a") as policy:
        policy.write("\n" + HOOK_PYTHON)

    def pin_python(components):
        del components["pre-commit-hooks"]
        components["python"] = {"versions": {"3.15.2": PYTHON}}

    edit_lock(root, pin_python)
    policy = load_policy_tree(root)
    hygiene, _ = compiled(policy, ("hygiene",))
    python, _ = compiled(policy, ("hygiene", "python"))
    assert (set(hygiene["components"]), set(hygiene["native_artifacts"])) == (
        set(),
        {"betterleaks"},
    )
    assert set(python["components"]) == {"python", "ruff"}


class Releases:
    """Release files by URL, where gateseal policy lock would download them."""

    def __init__(self, files):
        self.files = files

    def tagged(self, repository, tag):
        return None

    def download(self, url):
        return self.files.get(url)


def release_files(line):
    """Return Betterleaks 1.9.0's archives and a listing of line for each."""
    archives = {
        name: name.encode()
        for name in (
            "betterleaks_1.9.0_darwin_arm64.tar.gz",
            "betterleaks_1.9.0_linux_x64.tar.gz",
        )
    }
    text = "".join(line(name, data) for name, data in archives.items())
    return {BETTERLEAKS + "checksums.txt": text.encode()} | {
        BETTERLEAKS + name: data for name, data in archives.items()
    }


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_a_listing_line_as_sha256sum_writes_it_locks_its_archive():
    tool = built_in_tools()["betterleaks"]
    files = release_files(lambda name, data: f"{sha(data)} *{name}\n")
    assert tool.source.lock(tool, "1.9.0", None, Releases(files)) == {
        "platforms": {
            platform: {
                "executable": "betterleaks",
                "sha256": sha(f"betterleaks_1.9.0_{name}.tar.gz".encode()),
                "url": f"{BETTERLEAKS}betterleaks_1.9.0_{name}.tar.gz",
            }
            for platform, name in (
                ("linux-x64", "linux_x64"),
                ("macos-arm64", "darwin_arm64"),
            )
        }
    }


def test_a_listing_line_without_a_sha256_lists_no_archive():
    tool = built_in_tools()["betterleaks"]
    files = release_files(
        lambda name, data: (
            f"{sha(data).upper() if 'linux' in name else sha(data)}  {name}\n"
        )
    )
    with pytest.raises(GatesealError) as failure:
        tool.source.lock(tool, "1.9.0", None, Releases(files))
    assert failure.value.code == "upstream_checksum_missing"
    named = ("checksums.txt", "betterleaks_1.9.0_linux_x64.tar.gz", "linux-x64")
    assert all(value in str(failure.value) for value in named)
