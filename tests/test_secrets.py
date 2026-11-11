import json
import os
import platform
import shutil
from pathlib import Path

import pytest
from conftest import (
    attempts,
    overwrite,
    prepared_state,
    preserved,
    published,
    recorded_tree,
)
from installation import clone, process, success

pytestmark = pytest.mark.integration

SECRET = "AKIA" + "Q7XK2M9P4R8T6W3Z"
TRACKED = (
    "gateseal.toml",
    "gateseal.lock",
    ".pre-commit-config.yaml",
    ".gateseal/betterleaks-history-report.tmpl",
    ".gateseal/betterleaks-report.tmpl",
    ".gateseal/betterleaks.toml",
    ".gateseal/mise.toml",
)
ACTIVATE = "Next: Run gateseal activate, then retry."
REMOVE = "Next: Remove each secret, stage the fix, and retry."
# pre-commit's virtualenv and pip, which build the hygiene environment, and
# Betterleaks, for its compiled regular expressions, write these under HOME.
USER_CACHES = (
    (
        "Library/Caches/pip",
        "Library/Caches/virtualenv",
        "Library/Caches/com.github.wasilibs",
    )
    if platform.system() == "Darwin"
    else (".cache/pip", ".cache/virtualenv", ".cache/com.github.wasilibs")
)


def configure(installed, root, *capabilities, code=0):
    flags = [option for name in capabilities for option in ("--capability", name)]
    return installed.cli(
        root,
        "configure",
        *flags,
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
        code=code,
    )


def adopt(installed, root, env=None):
    configure(installed, root, "hygiene", "secrets")
    assert installed.cli(root, "activate", env=env)["code"] == "prepared"
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "-m", "Adopt secret detection"], root, env)
    return root


@pytest.fixture
def secured(installed, consumer):
    return adopt(installed, consumer)


@pytest.fixture(scope="module")
def guarded(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("guarded consumer ü").resolve()
    return adopt(installed, clone(root / "origin", root / "consumer space ü "))


def stage_secret(root, name="leak.txt"):
    (root / name).write_text(f"key = {SECRET}\n")
    success(["git", "add", name], root)


def unstage(root, name="leak.txt"):
    success(["git", "rm", "--cached", "-q", "--ignore-unmatch", name], root)
    (root / name).unlink(missing_ok=True)


def assert_blocked(result, name="leak.txt"):
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert f"{name}:1:7: aws-access-token" in output, output
    assert REMOVE in output, output
    assert SECRET not in output


def script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return path


def fakes(directory, sentinels, names=("betterleaks", "gateseal-betterleaks")):
    """Return a directory of host executables that touch a sentinel and exit 0."""
    for name in names:
        script(directory / name, f"touch {str(sentinels / name)!r}\nexit 0\n")
    return directory


def without_mise(env, directory):
    """Return env whose PATH reaches every executable it reached but mise."""
    entries = []
    for index, entry in enumerate(env["PATH"].split(os.pathsep)):
        if not (Path(entry) / "mise").exists():
            entries.append(entry)
            continue
        mirror = directory / f"path {index}"
        mirror.mkdir(parents=True)
        for child in Path(entry).iterdir():
            if child.name != "mise":
                (mirror / child.name).symlink_to(child)
        entries.append(str(mirror))
    path = os.pathsep.join(entries)
    assert shutil.which("mise", path=path) is None
    return dict(env, PATH=path)


def mise_shim(installed, directory, body):
    """Return an environment whose mise runs body, then the real mise."""
    real = shutil.which("mise", path=installed.environment["PATH"])
    script(directory / "mise", body.format(mise=real) + f'exec "{real}" "$@"\n')
    path = os.pathsep.join([str(directory), installed.environment["PATH"]])
    return dict(installed.environment, PATH=path)


def test_staged_secret_stops_the_commit_and_a_clean_one_passes(installed, consumer):
    configure(installed, consumer, "hygiene", "secrets")
    for name in TRACKED:
        assert not (consumer / name).stat().st_mode & 0o111, name
        assert b"\r" not in (consumer / name).read_bytes()
    assert installed.cli(consumer, "check")["code"] == "consistent"
    assert installed.cli(consumer, "activate")["code"] == "prepared"
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert list(lock["outputs"]) == sorted(TRACKED[2:])
    assert lock["native_artifacts"]["betterleaks"]["version"] == "1.9.0"
    success(["git", "add", *TRACKED], consumer)
    success(["git", "commit", "-m", "Adopt secret detection"], consumer)
    stage_secret(consumer)
    assert_blocked(process(["git", "commit", "-m", "Leak"], consumer))
    assert success(["git", "log", "--format=%s"], consumer).stdout == (
        "Adopt secret detection\n"
    )
    (consumer / "leak.txt").write_text("key = rotated\n")
    success(["git", "add", "leak.txt"], consumer)
    committed = process(["git", "commit", "-m", "Clean"], consumer)
    assert committed.returncode == 0, committed.stdout + committed.stderr
    from gateseal.bootstrap import PreparedRecord
    from gateseal.validation import canonical

    attempt = published(consumer)
    document = PreparedRecord.read(attempt).document()
    assert canonical(document) == (attempt / "prepared.json").read_bytes()


def test_a_file_named_like_an_option_cannot_silence_betterleaks(installed, guarded):
    option = guarded / "--exit-code=0"
    option.write_text("clean\n")
    success(["git", "add", "--", option.name], guarded)
    stage_secret(guarded)
    try:
        assert_blocked(process(["git", "commit", "-m", "Leak"], guarded))
        assert installed.cli(guarded, "run", code=1)["code"] == "checks_failed"
    finally:
        unstage(guarded)
        success(["git", "rm", "--cached", "-q", "--", option.name], guarded)
        option.unlink()


def test_two_secrets_on_one_line_both_stay_hidden(installed, guarded):
    other = "AKIA" + "ZZ3K7M2P9R4T8W6Q"
    path = guarded / "leak.txt"
    path.write_text(f"key = {SECRET} backup = {other}\n")
    success(["git", "add", path.name], guarded)
    try:
        committed = process(["git", "commit", "-m", "Two keys"], guarded)
        assert committed.returncode == 1, committed.stdout + committed.stderr
        manual = process(
            [installed.command, "--repo", guarded, "--json", "--verbose", "run"],
            guarded,
            installed.environment,
        )
        assert manual.returncode == 1, manual.stdout + manual.stderr
        for output in (committed.stdout + committed.stderr, manual.stdout):
            assert SECRET not in output and other not in output
            assert "leak.txt:1:7: aws-access-token" in output, output
            assert "leak.txt:1:37: aws-access-token" in output, output
    finally:
        unstage(guarded)


def test_a_token_under_a_quoted_json_key_blocks_the_commit(installed, guarded):
    token = "Zq8vN3kL7pR2" + "xW9mT4bY6cJ1"
    path = guarded / "settings.json"
    path.write_text(f'{{"api_key": "{token}"}}\n')
    success(["git", "add", path.name], guarded)
    try:
        committed = process(["git", "commit", "-m", "Quoted key"], guarded)
        output = committed.stdout + committed.stderr
        assert committed.returncode == 1, output
        assert "settings.json:1:3: generic-api-key" in output, output
        assert token not in output
    finally:
        unstage(guarded, path.name)


def test_commit_scans_staged_content_and_run_scans_the_worktree(installed, guarded):
    path = guarded / "partial.txt"
    path.write_text("clean\n")
    success(["git", "add", path.name], guarded)
    path.write_text(f"clean\nkey = {SECRET}\n")
    try:
        success(["git", "commit", "-m", "Partially staged"], guarded)
        assert path.read_text() == f"clean\nkey = {SECRET}\n"
        committed = success(["git", "show", "HEAD:partial.txt"], guarded).stdout
        assert committed == "clean\n"
        result = process(
            [installed.command, "--repo", guarded, "--json", "--verbose", "run"],
            guarded,
            installed.environment,
        )
        assert result.returncode == 1, result.stdout + result.stderr
        document = json.loads(result.stdout)
        assert document["code"] == "checks_failed"
        details = document["diagnostics"][1]["message"]
        assert "partial.txt:2:7: aws-access-token" in details, details
        assert SECRET not in result.stdout
    finally:
        path.write_text("clean\n")
    assert installed.cli(guarded, "run")["code"] == "checks_passed"


def test_commit_all_scans_the_unstaged_secret_it_commits(installed, guarded):
    path = guarded / "tracked.txt"
    path.write_text("clean\n")
    success(["git", "add", path.name], guarded)
    success(["git", "commit", "-m", "Track a file"], guarded)
    path.write_text(f"clean\nkey = {SECRET}\n")
    try:
        committed = process(["git", "commit", "-a", "-m", "Commit all"], guarded)
        output = committed.stdout + committed.stderr
        assert committed.returncode == 1, output
        assert "tracked.txt:2:7: aws-access-token" in output, output
        assert SECRET not in output
        assert success(["git", "diff", "--cached", "--name-only"], guarded).stdout == ""
    finally:
        path.write_text("clean\n")


def hostile(sentinels, label, old_url):
    touch = f"touch {sentinels}/{label}"
    return f"""
[tools]
"http:betterleaks" = {{ version = "1.7.0", url = "{old_url}" }}

[env]
HOSTILE = "{{{{ exec(command='{touch}-template') }}}}"
_.source = "{sentinels.parent}/source-{label}.sh"

[hooks]
preinstall = "{touch}-preinstall"
postinstall = "{touch}-postinstall"
enter = "{touch}-enter"

[settings]
experimental = true

[tasks.install]
run = "{touch}-task"
"""


def snapshot(home, *roots):
    """Return every path under home and roots with its bytes, except user caches."""
    caches = [home / cache for cache in USER_CACHES]
    return {
        path: (path.read_bytes() if path.is_file() else None)
        for root in (home, *roots)
        for path in [root, *root.rglob("*")]
        if not any(
            path.is_relative_to(cache)
            or (home in path.parents and cache.is_relative_to(path))
            for cache in caches
        )
    }


def changes(before, after):
    return sorted(
        str(path)
        for path in before.keys() | after.keys()
        if before.get(path, 0) != after.get(path, 0)
    )


def test_activation_ignores_caller_mise_configuration(installed, consumer, tmp_path):
    configure(installed, consumer, "hygiene", "secrets")
    planted = tmp_path / "planted"
    sentinels = planted / "sentinels"
    sentinels.mkdir(parents=True)
    old_url = (
        "https://github.com/betterleaks/betterleaks/releases/download/v1.7.0/"
        "betterleaks_1.7.0_linux_x64.tar.gz"
    )
    home = tmp_path / "home"
    configs = {
        consumer / "mise.toml": "project",
        consumer / ".mise.toml": "dot",
        consumer / ".config/mise.toml": "dotconfig",
        consumer / "mise.local.toml": "local",
        consumer / "mise.hostile.toml": "environment",
        consumer.parent / "mise.toml": "parent",
        home / ".config/mise/config.toml": "global",
        home / ".config/mise/conf.d/extra.toml": "confd",
        planted / "config-file.toml": "config-file",
    }
    for path, label in configs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(hostile(sentinels, label, old_url))
        script(planted / f"source-{label}.sh", f"touch {sentinels}/{label}-source\n")
    (consumer / ".tool-versions").write_text("http:betterleaks 1.7.0\n")
    installs = {
        "MISE_DATA_DIR": planted / "data",
        "MISE_SYSTEM_DATA_DIR": planted / "system",
        "MISE_SHARED_INSTALL_DIRS": planted / "shared",
    }
    for name, directory in installs.items():
        version = directory / ("" if name == "MISE_SHARED_INSTALL_DIRS" else "installs")
        fakes(version / "http-betterleaks" / "1.9.0", sentinels, ["betterleaks"])
    host = fakes(tmp_path / "host bin", sentinels)
    env = dict(
        installed.environment,
        HOME=str(home),
        PATH=os.pathsep.join([str(host), installed.environment["PATH"]]),
        MISE_ENV="hostile",
        MISE_CONFIG_FILE=str(planted / "config-file.toml"),
        MISE_GLOBAL_CONFIG_FILE=str(planted / "config-file.toml"),
        MISE_TRUSTED_CONFIG_PATHS=str(tmp_path),
        MISE_YES="1",
        **{name: str(directory) for name, directory in installs.items()},
    )
    planted_in_clone = [path for path in configs if path.is_relative_to(consumer)]
    before = snapshot(home, planted, *planted_in_clone)
    assert installed.cli(consumer, "activate", env=env)["code"] == "prepared"
    assert sorted(path.name for path in sentinels.iterdir()) == []
    executable = Path(
        prepared_state(consumer)["native_tools"]["betterleaks"]["executable"]
    )
    assert executable.is_relative_to(published(consumer))
    version = success([executable, "version"], consumer).stdout.strip()
    assert version == "1.9.0"
    success(["git", "add", *TRACKED], consumer)
    success(["git", "commit", "-m", "Adopt"], consumer, env)
    stage_secret(consumer)
    assert_blocked(process(["git", "commit", "-m", "Leak"], consumer, env))
    assert sorted(path.name for path in sentinels.iterdir()) == []
    assert changes(before, snapshot(home, planted, *planted_in_clone)) == []
    control = process(
        ["mise", "config", "ls"],
        consumer,
        dict(env, PATH=installed.environment["PATH"], MISE_OFFLINE="1"),
    )
    assert control.returncode == 0, control.stdout + control.stderr
    assert (sentinels / "project-template").exists(), "the planted configs are inert"


@pytest.mark.parametrize("damage", ["removed", "not executable"])
def test_damaged_betterleaks_never_falls_back_to_a_host_one(
    installed, secured, tmp_path, damage
):
    sentinels = tmp_path / "sentinels"
    sentinels.mkdir()
    host = fakes(tmp_path / "host bin", sentinels)
    env = dict(
        installed.environment,
        PATH=os.pathsep.join([str(host), installed.environment["PATH"]]),
    )
    state = prepared_state(secured)
    executable = Path(state["native_tools"]["betterleaks"]["executable"])
    if damage == "removed":
        executable.unlink()
    else:
        executable.chmod(0o644)
    stage_secret(secured)
    committed = process(["git", "commit", "-m", "Leak"], secured, env)
    assert committed.returncode != 0
    assert committed.stderr.startswith("gateseal: "), committed.stderr
    assert ACTIVATE in committed.stderr
    assert installed.cli(secured, "doctor", code=3, env=env)["code"] == (
        "needs_activation"
    )
    assert installed.cli(secured, "run", code=3, env=env)["code"] == "needs_activation"
    only_betterleaks = fakes(tmp_path / "host betterleaks", sentinels, ["betterleaks"])
    native_path = [state["paths"]["tools"], str(only_betterleaks)]
    native = process(
        [state["paths"]["pre_commit_python"], "-I", "-B", "-m", "pre_commit", "run"],
        secured,
        dict(
            installed.environment,
            PRE_COMMIT_HOME=state["paths"]["pre_commit_home"],
            PATH=os.pathsep.join([*native_path, installed.environment["PATH"]]),
        ),
    )
    assert native.returncode != 0
    assert "Executable `gateseal-betterleaks` not found" in native.stdout
    assert list(sentinels.iterdir()) == []
    assert installed.cli(secured, "activate", env=env)["code"] == "prepared"
    assert_blocked(process(["git", "commit", "-m", "Leak"], secured, env))
    assert list(sentinels.iterdir()) == []


def test_commits_and_inspection_never_start_mise(installed, guarded, tmp_path):
    sentinel = tmp_path / "mise started"
    shim = script(tmp_path / "sentinel bin" / "mise", f"touch {str(sentinel)!r}\n")
    env = without_mise(installed.environment, tmp_path / "mirror")
    env["PATH"] = os.pathsep.join([str(shim.parent), env["PATH"]])
    (guarded / "clean.txt").write_text("clean\n")
    success(["git", "add", "clean.txt"], guarded)
    success(["git", "commit", "-m", "Clean without mise"], guarded, env)
    stage_secret(guarded)
    try:
        assert_blocked(process(["git", "commit", "-m", "Leak"], guarded, env))
        assert installed.cli(guarded, "run", code=1, env=env)["code"] == "checks_failed"
    finally:
        unstage(guarded)
    assert installed.cli(guarded, "run", env=env)["code"] == "checks_passed"
    assert installed.cli(guarded, "status", env=env)["code"] == "recorded"
    assert installed.cli(guarded, "doctor", env=env)["code"] == "healthy"
    assert not sentinel.exists()


def test_missing_or_old_mise_publishes_nothing(installed, consumer, tmp_path):
    configure(installed, consumer, "hygiene", "secrets")
    missing = installed.cli(
        consumer,
        "activate",
        code=4,
        env=without_mise(installed.environment, tmp_path / "mirror"),
    )
    assert missing["code"] == "missing_prerequisite"
    assert missing["diagnostics"][0]["message"] == (
        "Install the documented mise prerequisite."
    )
    old = mise_shim(
        installed, tmp_path / "old", 'echo "2026.1.0 linux-x64 (2026-01-01)"\nexit 0\n'
    )
    older = installed.cli(consumer, "activate", code=4, env=old)
    assert older["code"] == "mise_version_mismatch"
    assert older["diagnostics"][0]["message"] == (
        "gateseal requires mise 2026.10.19 or later."
    )
    assert not (consumer / ".git/gateseal/current").is_symlink()
    assert attempts(consumer) == set()


def test_failed_tool_preparation_keeps_the_published_attempt(
    installed, secured, tmp_path
):
    before = published(secured).name
    kept = attempts(secured)
    failing = mise_shim(
        installed, tmp_path / "failing", '[ "$2" = install ] && exit 7\n'
    )
    fake = mise_shim(
        installed,
        tmp_path / "fake",
        'directory="$MISE_DATA_DIR/installs/http-betterleaks/1.9.0"\n'
        'case "$2" in\n'
        '  install) mkdir -p "$directory"\n'
        "    printf '#!/bin/sh\\necho 1.8.0\\n' > \"$directory/betterleaks\"\n"
        '    chmod 755 "$directory/betterleaks"; exit 0 ;;\n'
        '  where) echo "$directory"; exit 0 ;;\n'
        "esac\n",
    )
    compiler = recorded_tree(prepared_state(secured), "compiler_python")
    installer = next(compiler.glob("lib/python3.14/site-packages/gateseal-*/INSTALLER"))
    with preserved(installer) as original:
        overwrite(installer, original.upper())
        failed = installed.cli(secured, "activate", code=4, env=failing)
        assert failed["code"] == "tool_failed"
        assert failed["next_steps"] == ["gateseal activate"]
        wrong = installed.cli(secured, "activate", code=4, env=fake)
        assert wrong["code"] == "tool_failed"
        assert "reports version 1.8.0, not 1.9.0" in json.dumps(wrong)
        assert published(secured).name == before
        assert attempts(secured) == kept
        (secured / "clean.txt").write_text("clean\n")
        success(["git", "add", "clean.txt"], secured)
        success(["git", "commit", "-m", "Previous attempt"], secured)
        stage_secret(secured)
        assert_blocked(process(["git", "commit", "-m", "Leak"], secured))


def test_hygiene_activates_without_mise(installed, configured, tmp_path):
    env = without_mise(installed.environment, tmp_path / "mirror")
    assert installed.cli(configured, "activate", env=env)["code"] == "prepared"
    assert prepared_state(configured)["native_tools"] == {}
    assert list(Path(prepared_state(configured)["paths"]["tools"]).iterdir()) == []


def test_activation_is_idempotent_and_reuses_the_mise_cache(
    installed, guarded, tmp_path
):
    kept = attempts(guarded)
    again = installed.cli(guarded, "activate")
    assert (again["code"], again["changed"]) == ("already_prepared", [])
    assert attempts(guarded) == kept
    second = tmp_path / "second clone"
    success(["git", "clone", guarded, second], tmp_path)
    success(["git", "config", "user.name", "Second"], second)
    success(["git", "config", "user.email", "second@example.invalid"], second)
    offline = mise_shim(installed, tmp_path / "offline", "export MISE_OFFLINE=1\n")
    assert installed.cli(second, "activate", env=offline)["code"] == "prepared"
    stage_secret(second)
    assert_blocked(process(["git", "commit", "-m", "Leak"], second))


def test_minimal_path_commit_detects_secrets(installed, guarded):
    minimal = dict(installed.environment, PATH="/usr/bin:/bin")
    (guarded / "minimal.txt").write_text("minimal\n")
    success(["git", "add", "minimal.txt"], guarded)
    success(["/usr/bin/git", "commit", "-m", "Minimal PATH"], guarded, minimal)
    stage_secret(guarded)
    try:
        assert_blocked(
            process(["/usr/bin/git", "commit", "-m", "Leak"], guarded, minimal)
        )
    finally:
        unstage(guarded)


def test_configure_follows_the_manifest(installed, configured):
    before = {name: (configured / name).read_bytes() for name in TRACKED[:3]}
    refused = configure(installed, configured, "hygiene", "secrets", code=2)
    assert refused["code"] == "configure_assertion_failed"
    assert refused["next_steps"] == [
        "Edit capabilities in gateseal.toml, then run gateseal configure."
    ]
    assert before == {name: (configured / name).read_bytes() for name in TRACKED[:3]}
    assert not (configured / ".gateseal").exists()
    manifest = configured / "gateseal.toml"
    manifest.write_text(
        manifest.read_text().replace('["hygiene"]', '["secrets", "hygiene"]')
    )
    assert installed.cli(configured, "configure")["changed"] == [
        "gateseal.lock",
        ".gateseal/betterleaks-history-report.tmpl",
        ".gateseal/betterleaks-report.tmpl",
        ".gateseal/betterleaks.toml",
        ".gateseal/mise.toml",
        ".pre-commit-config.yaml",
    ]
    assert installed.cli(configured, "check")["code"] == "consistent"
    assert installed.cli(configured, "configure")["changed"] == []
    assert installed.cli(configured, "activate")["code"] == "prepared"
    success(["git", "add", *TRACKED], configured)
    success(["git", "commit", "-m", "Add secret detection"], configured)
    rules = configured / ".gateseal/betterleaks.toml"
    with preserved(rules) as original:
        rules.write_bytes(original.replace(b"aws-access-token", b"aws-renamed"))
        for command in ("check", "configure"):
            edited = installed.cli(configured, command, code=2)
            assert edited["code"] == "ownership_conflict"
            assert ".gateseal/betterleaks.toml" in json.dumps(edited)
        success(["git", "add", rules], configured)
        stage_secret(configured)
        committed = process(["git", "commit", "-m", "Edited rules"], configured)
        assert committed.returncode != 0
        assert (
            "gateseal: Restore the owned .gateseal/betterleaks.toml; "
            "it has been edited."
        ) in committed.stderr
    success(["git", "add", rules], configured)
    assert_blocked(process(["git", "commit", "-m", "Leak"], configured))


def test_a_hygiene_only_branch_asks_for_activation(installed, configured):
    assert installed.cli(configured, "activate")["code"] == "prepared"
    success(["git", "add", *TRACKED[:3]], configured)
    success(["git", "commit", "-m", "Adopt hygiene"], configured)
    success(["git", "branch", "hygiene"], configured)
    manifest = configured / "gateseal.toml"
    manifest.write_text(
        manifest.read_text().replace('["hygiene"]', '["hygiene", "secrets"]')
    )
    installed.cli(configured, "configure")
    assert installed.cli(configured, "activate")["code"] == "prepared"
    success(["git", "add", *TRACKED], configured)
    success(["git", "commit", "-m", "Add secret detection"], configured)
    success(["git", "switch", "-q", "hygiene"], configured)
    assert not (configured / ".gateseal").exists()
    stopped = process(["git", "commit", "--allow-empty", "-m", "Hygiene"], configured)
    assert stopped.returncode == 1, stopped.stderr
    assert (
        "gateseal: Staged gateseal.lock differs from the prepared runtime."
    ) in stopped.stderr
    assert ACTIVATE in stopped.stderr
    assert "Missing" not in stopped.stderr
    assert installed.cli(configured, "activate")["code"] == "prepared"
    success(["git", "commit", "--allow-empty", "-m", "Hygiene"], configured)


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_configure_refuses_an_unowned_gateseal_directory(
    installed, consumer, tmp_path, kind
):
    outside = tmp_path / "outside"
    outside.mkdir()
    owned = consumer / ".gateseal"
    if kind == "symlink":
        owned.symlink_to(outside, target_is_directory=True)
    else:
        owned.write_text("not a directory\n")
    refused = configure(installed, consumer, "hygiene", "secrets", code=2)
    assert refused["code"] == "unsafe_path"
    assert list(outside.iterdir()) == []
    assert not (consumer / "gateseal.lock").exists()
