import json
import os
import random
import shutil
from pathlib import Path
from string import ascii_letters, ascii_uppercase, digits

import pytest
from conftest import BUNDLED_TOOLS, on_machine, preserved
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
)
REMOVE = "Next: Remove each secret, stage the fix, and retry."
ALNUM = ascii_letters + digits
PIN = BUNDLED_TOOLS["betterleaks"].pin


def configure(installed, root, *capabilities, code=0):
    flags = [option for name in capabilities for option in ("--capability", name)]
    return installed.cli(
        root,
        "configure",
        *flags,
        code=code,
    )


def adopt(installed, root, env=None):
    configure(installed, root, "hygiene", "secrets")
    success(["git", "add", *TRACKED], root)
    success(["git", "commit", "-m", "Adopt secret detection"], root, env)
    return root


@pytest.fixture
def secured(installed, consumer):
    return adopt(installed, consumer)


@pytest.fixture(scope="module")
def guarded(installed, tmp_path_factory):
    root = tmp_path_factory.mktemp("guarded consumer ü").resolve()
    with on_machine(installed):
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


def checks(result):
    """Return each check's status from a status or doctor result."""
    return {
        entry["check"]: entry["status"]
        for entry in result["diagnostics"]
        if entry["category"] == "check"
    }


def test_staged_secret_stops_the_commit_and_a_clean_one_passes(installed, consumer):
    configure(installed, consumer, "hygiene", "secrets")
    for name in TRACKED:
        assert not (consumer / name).stat().st_mode & 0o111, name
        assert b"\r" not in (consumer / name).read_bytes()
    assert installed.cli(consumer, "check")["code"] == "consistent"
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert list(lock["outputs"]) == sorted(TRACKED[2:])
    assert lock["native_artifacts"]["betterleaks"]["version"] == PIN
    success(["git", "add", *TRACKED], consumer)
    success(["git", "commit", "-m", "Adopt secret detection"], consumer)
    assert success(["git", "status", "--porcelain"], consumer).stdout == ""
    stage_secret(consumer)
    assert_blocked(process(["git", "commit", "-m", "Leak"], consumer))
    assert success(["git", "log", "--format=%s"], consumer).stdout == (
        "Adopt secret detection\n"
    )
    (consumer / "leak.txt").write_text("key = rotated\n")
    success(["git", "add", "leak.txt"], consumer)
    committed = process(["git", "commit", "-m", "Clean"], consumer)
    assert committed.returncode == 0, committed.stdout + committed.stderr


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


def common_secrets():
    """Return a file of each common secret form, and every random part of them.

    The parts are drawn at run time, so this file holds no secret of its own.
    """
    chars, drawn = random.Random(92), []

    def token(length, alphabet=ALNUM):
        drawn.append("".join(chars.choice(alphabet) for _ in range(length)))
        return drawn[-1]

    def pem(kind):
        body = "\n".join(token(64, ALNUM + "+/") for _ in range(6))
        label = f"{kind}PRIVATE KEY-----"
        return f"-----BEGIN {label}\n{body}\n-----END {label}\n"

    aws, password = token(40, ALNUM + "+/"), token(16)
    client, auth = token(32), token(32)
    slack = f"xoxb-{token(12, digits)}-{token(13, digits)}-{token(24)}"
    stripe, npm = "sk_" + "live_" + token(24), "npm_" + token(36)
    plus, slash = "+" + token(39, ALNUM + "+/"), "/" + token(39, ALNUM + "+/")
    key_id = "AKIA" + token(12, ascii_uppercase + digits) + "NULL"
    github = "ghp_" + token(10) + "false" + token(21)
    fine_grained = f"github_pat_{token(22)}_{token(59)}"
    files = {
        "aws.ini": f"[default]\naws_secret_access_key = {aws}\n",
        "aws.env": f"AWS_SECRET_ACCESS_KEY={aws}\n",
        "aws.json": f'{{"SecretAccessKey": "{aws}"}}\n',
        "rsa.pem": pem("RSA "),
        "openssh.key": pem("OPENSSH "),
        "pkcs8.pem": pem(""),
        "settings.py": f'password = "{password}"\n',
        "db.env": f"DB_PASSWORD={password}\n",
        "oauth.py": f'client_secret = "{client}"\n',
        "auth.py": f'auth_token = "{auth}"\n',
        "slack.txt": f"bot = {slack}\n",
        "stripe.txt": f"key = {stripe}\n",
        ".npmrc": f"//registry.npmjs.org/:_authToken={npm}\n",
        "plus.py": f'api_key = "{plus}"\n',
        "slash.ini": f"aws_secret_access_key = {slash}\n",
        "key_id.ini": f"aws_access_key_id = {key_id}\n",
        "github.txt": f"token = {github}\n",
        "fine_grained.txt": f"token = {fine_grained}\n",
    }
    return files, drawn


def test_common_secret_forms_each_stop_the_commit(guarded):
    files, drawn = common_secrets()
    for name, text in files.items():
        (guarded / name).write_text(text)
    success(["git", "add", *files], guarded)
    try:
        committed = process(["git", "commit", "-m", "Common secrets"], guarded)
        output = committed.stdout + committed.stderr
        assert committed.returncode == 1, output
        for finding in (
            ".npmrc:1:34: npm-access-token",
            "auth.py:1:1: generic-api-key",
            "aws.env:1:5: generic-api-key",
            "aws.ini:2:5: generic-api-key",
            "aws.json:1:3: generic-api-key",
            "db.env:1:4: generic-password",
            "fine_grained.txt:1:9: github-fine-grained-pat",
            "github.txt:1:9: github-pat",
            "key_id.ini:1:21: aws-access-token",
            "oauth.py:1:8: generic-api-key",
            "openssh.key:1:1: private-key",
            "pkcs8.pem:1:1: private-key",
            "plus.py:1:1: generic-base64-key",
            "rsa.pem:1:1: private-key",
            "settings.py:1:1: generic-password",
            "slack.txt:1:7: slack-bot-token",
            "slash.ini:1:5: generic-base64-key",
            "stripe.txt:1:7: stripe-access-token",
        ):
            assert finding in output, output
        assert not [part for part in drawn if part in output]
    finally:
        for name in files:
            unstage(guarded, name)


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


def test_two_repositories_that_pin_different_versions_both_commit(
    installed, guarded, secured
):
    """mise keeps one install of each version, and each entry names its own."""
    flags = ("--reason", "Upstream issue 123", "--review-by", "2099-12-31")
    installed.cli(secured, "upgrade", "betterleaks=1.8.0", *flags)
    success(["git", "commit", "-a", "-m", "Keep Betterleaks 1.8.0"], secured)
    versions = {}
    for root in (secured, guarded, secured):
        config = (root / ".pre-commit-config.yaml").read_text()
        versions[root] = next(v for v in ("1.8.0", PIN) if f"]@{v}" in config)
        stage_secret(root)
        try:
            assert_blocked(process(["git", "commit", "-m", "Leak"], root))
        finally:
            unstage(root)
    assert versions == {secured: "1.8.0", guarded: PIN}


HOSTILE = """
[tools]
"http:betterleaks" = {{ version = "1.7.0", url = "{url}" }}

[env]
HOSTILE = "{{{{ exec(command='touch {sentinels}/{label}-template') }}}}"

[hooks]
preinstall = "touch {sentinels}/{label}-preinstall"
enter = "touch {sentinels}/{label}-enter"

[settings]
exec_auto_install = false
"""
"""A mise file that names another Betterleaks, runs commands and stops installs."""


def test_a_commit_ignores_mise_files_and_a_host_betterleaks(
    installed, consumer, tmp_path
):
    """Only the hook's entry names the binary: no mise file and no PATH entry does."""
    sentinels = tmp_path / "sentinels"
    sentinels.mkdir()
    url = (
        "https://github.com/betterleaks/betterleaks/releases/download/v1.7.0/"
        "betterleaks_1.7.0_linux_x64.tar.gz"
    )
    home = tmp_path / "home"
    configs = {
        consumer / "mise.toml": "project",
        consumer / ".config/mise/conf.d/extra.toml": "confd",
        home / ".config/mise/config.toml": "global",
    }
    for path, label in configs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(HOSTILE.format(url=url, sentinels=sentinels, label=label))
    host = script(tmp_path / "host bin/betterleaks", f"touch {sentinels}/host\n")
    env = dict(
        installed.environment,
        HOME=str(home),
        PATH=os.pathsep.join([installed.environment["PATH"], str(host.parent)]),
        MISE_TRUSTED_CONFIG_PATHS=str(tmp_path),
        MISE_YES="1",
    )
    adopt(installed, consumer, env)
    stage_secret(consumer)
    assert_blocked(process(["git", "commit", "-m", "Leak"], consumer, env))
    assert sorted(path.name for path in sentinels.iterdir()) == []
    control = process(["mise", "config", "ls"], consumer, dict(env, MISE_OFFLINE="1"))
    assert control.returncode == 0, control.stdout + control.stderr
    assert (sentinels / "project-template").exists(), "the planted files are inert"


def test_status_and_doctor_check_mise_for_a_release_binary(
    installed, guarded, tmp_path
):
    for command in ("status", "doctor"):
        result = installed.cli(guarded, command)
        assert (result["code"], checks(result)["mise"]) == ("healthy", "passed")
    missing = without_mise(installed.environment, tmp_path / "mirror")
    assert checks(installed.cli(guarded, "status", env=missing))["mise"] == "failed"
    doctor = installed.cli(guarded, "doctor", code=3, env=missing)
    assert (doctor["code"], checks(doctor)["mise"]) == ("unhealthy", "failed")
    assert doctor["next_steps"] == ["Install mise 2026.10.19 or later."]
    off = dict(installed.environment, MISE_EXEC_AUTO_INSTALL="0")
    warned = installed.cli(guarded, "doctor", env=off)
    [warning] = [e for e in warned["diagnostics"] if e["severity"] == "warning"]
    assert warning["category"] == "mise_auto_install_off"
    assert warned["next_steps"] == ["Unset MISE_EXEC_AUTO_INSTALL."]
    assert "warning" not in json.dumps(installed.cli(guarded, "status", env=off))


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
        ".pre-commit-config.yaml",
    ]
    assert installed.cli(configured, "check")["code"] == "consistent"
    assert installed.cli(configured, "configure")["changed"] == []
    rules = configured / ".gateseal/betterleaks.toml"
    with preserved(rules) as original:
        rules.write_bytes(original.replace(b"aws-access-token", b"aws-renamed"))
        for command in ("check", "configure"):
            edited = installed.cli(configured, command, code=2)
            assert edited["code"] == "ownership_conflict"
            assert ".gateseal/betterleaks.toml" in json.dumps(edited)
    success(["git", "add", *TRACKED], configured)
    success(["git", "commit", "-m", "Add secret detection"], configured)
    stage_secret(configured)
    assert_blocked(process(["git", "commit", "-m", "Leak"], configured))


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
