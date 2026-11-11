"""Adopt a team-owned Git policy through the installed command.

The policy repository is a real local Git repository that the canonical https
URL reaches through url.<base>.insteadOf, as a laptop's Git configuration would
map it, so gateseal stores only the credential-free URL.
"""

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from conftest import TRACKED, git_path
from gateseal import sources
from gateseal.manifest import GitSource
from installation import clone, success
from team_policy import POLICY, URL, environment, git, publish


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """A policy repository with authoring scripts beside its policy data."""
    origin = tmp_path_factory.mktemp("team policy ü") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    script = origin / "bin" / "derive.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho derive\n")
    script.chmod(0o755)
    (origin / "README.md").symlink_to("policy/policy.toml")
    first = publish(origin, "v1")
    second = publish(origin, "v2", annotated=False)
    return origin, first, second


def configure(installed, root, env, *flags, code=0):
    return installed.cli(
        root,
        "configure",
        *flags,
        "--compiler-wheel",
        installed.wheel,
        "--compiler-sha256",
        installed.checksum,
        code=code,
        env=env,
    )


def adopt(installed, root, env, tag="v1", *flags, code=0):
    return configure(
        installed,
        root,
        env,
        "--capability",
        "hygiene",
        "--policy",
        URL,
        "--policy-path",
        "policy",
        "--policy-tag",
        tag,
        *flags,
        code=code,
    )


def categories(result):
    return {d["category"]: d for d in result["diagnostics"]}


def tracked(root):
    return {
        name: (root / name).read_bytes() for name in TRACKED if (root / name).exists()
    }


def stored_policies(root):
    directory = git_path(root, "gateseal") / "policies"
    return sorted(directory.iterdir()) if directory.exists() else []


CHOICE_AND_EXCEPTION = """
[choices.hygiene]
exclude = ["vendor"]

[exceptions.pre-commit-hooks]
version = "5.0.0"
reason = "6.0.0 rejects our generated YAML; revisit with the generator upgrade"
review_by = 2026-01-15
"""


@pytest.fixture(scope="module")
def adopted(installed, team, tmp_path_factory):
    """A consumer that adopted v1 with a choice and an overdue exception."""
    origin, first, _ = team
    root = tmp_path_factory.mktemp("team consumer ü")
    consumer = clone(root / "origin", root / "consumer")
    env = environment(installed, origin)
    result = adopt(installed, consumer, env)
    assert categories(result)["policy_tag"]["message"] == (
        f"Tag v1 names commit {first}, as adoption requires."
    )
    manifest = consumer / "gateseal.toml"
    manifest.write_text(manifest.read_text() + CHOICE_AND_EXCEPTION)
    reconciled = installed.cli(consumer, "configure", env=env)
    assert "policy_tag" not in categories(reconciled)
    assert sorted(reconciled["changed"]) == [".pre-commit-config.yaml", "gateseal.lock"]
    assert installed.cli(consumer, "activate", env=env)["code"] == "prepared"
    success(["git", "add", *TRACKED], consumer)
    success(["git", "commit", "-m", "Adopt the team policy"], consumer)
    return consumer, env


def test_adoption_records_the_credential_free_identity(team, adopted):
    _, first, _ = team
    consumer, _ = adopted
    manifest = (consumer / "gateseal.toml").read_text()
    assert f'repository = "{URL}"' in manifest
    assert f'commit = "{first}"' in manifest and 'tag = "v1"' in manifest
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["policy"] == {
        "kind": "git",
        "repository": URL,
        "path": "policy",
        "commit": first,
        "tag": "v1",
        "release": POLICY.release,
        "digest": POLICY.content_digest,
    }
    assert lock["exceptions"] == {
        "pre-commit-hooks": {
            "version": "5.0.0",
            "pin": "6.0.0",
            "reason": "6.0.0 rejects our generated YAML; revisit with the "
            "generator upgrade",
            "review_by": "2026-01-15",
        }
    }
    assert lock["choices"] == {"hygiene": {"exclude": ["vendor"]}}
    assert lock["components"]["pre-commit-hooks"]["revision"] == (
        "cef0300fd0fc4d2a87a85fa2093c6b283ea36f4b"
    )
    assert str(team[0]) not in json.dumps(lock)


def test_status_and_doctor_show_choices_and_exceptions(installed, adopted):
    consumer, env = adopted
    before, stored = tracked(consumer), stored_policies(consumer)
    for command in ("status", "doctor"):
        result = installed.cli(consumer, command, env=env)
        found = categories(result)
        assert found["overdue_exception"]["severity"] == "warning"
        assert (
            "pre-commit-hooks 5.0.0 (policy pin 6.0.0)"
            in (found["overdue_exception"]["message"])
        )
        assert found["policy_choice"]["message"] == "Choice hygiene.exclude: vendor."
        assert "checked at adoption" in found["policy"]["message"]
        assert any("review_by" in step for step in result["next_steps"])
    assert tracked(consumer) == before and stored_policies(consumer) == stored


def test_commit_applies_the_choice_and_the_exception(installed, adopted):
    consumer, _ = adopted
    config = (consumer / ".pre-commit-config.yaml").read_text()
    assert "cef0300fd0fc4d2a87a85fa2093c6b283ea36f4b" in config
    vendored = consumer / "vendor" / "generated.txt"
    vendored.parent.mkdir(exist_ok=True)
    vendored.write_bytes(b"kept as generated  \n")
    owned = consumer / "notes.txt"
    owned.write_bytes(b"trailing space  \n")
    success(["git", "add", "vendor", owned.name], consumer)
    failed = subprocess.run(
        ["git", "commit", "-m", "Add files"], cwd=consumer, capture_output=True
    )
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert owned.read_bytes() == b"trailing space\n"
    assert vendored.read_bytes() == b"kept as generated  \n"
    success(["git", "add", owned.name], consumer)
    success(["git", "commit", "-m", "Add files"], consumer)


def test_moved_tag_is_descriptive_after_adoption(installed, team, adopted, tmp_path):
    origin, first, second = team
    consumer, env = adopted
    git(origin, "tag", "-f", "-a", "-m", "moved", "v1", second)
    try:
        assert installed.cli(consumer, "configure", env=env)["changed"] == []
        fresh = tmp_path / "fresh"
        success(["git", "clone", consumer, fresh], tmp_path)
        status = installed.cli(fresh, "status", env=env)
        assert status["code"] == "needs_activation"
        assert status["next_steps"] == ["gateseal activate"]
        assert stored_policies(fresh) == []
        assert installed.cli(fresh, "check", env=env)["code"] == "consistent"
        assert len(stored_policies(fresh)) == 1
        other = clone(tmp_path / "other origin", tmp_path / "other")
        refused = adopt(installed, other, env, "v1", "--policy-commit", first, code=2)
        assert refused["code"] == "policy_tag_mismatch"
        assert second in refused["diagnostics"][0]["message"]
        assert tracked(other) == {}
    finally:
        git(origin, "tag", "-f", "-a", "-m", "v1", "v1", first)


@pytest.mark.parametrize("conflict", ["custom_hooks_path", "unmanaged_hook"])
def test_uncached_policy_status_names_hook_conflicts(
    installed, team, consumer, tmp_path, conflict
):
    env = environment(installed, team[0])
    adopt(installed, consumer, env)
    git(consumer, "add", *TRACKED)
    git(consumer, "commit", "-m", "Adopt the team policy")
    fresh = tmp_path / "fresh"
    success(["git", "clone", consumer, fresh], tmp_path)
    before = tracked(fresh)
    hook = git_path(fresh, "hooks/pre-commit")
    foreign = b"#!/bin/sh\nexit 0\n"
    if conflict == "custom_hooks_path":
        git(fresh, "config", "core.hooksPath", "custom-hooks")
    else:
        hook.write_bytes(foreign)
    assert stored_policies(fresh) == []
    status = installed.cli(fresh, "status")
    assert status["code"] == conflict
    assert status["next_steps"] and status["next_steps"] != ["gateseal activate"]
    assert stored_policies(fresh) == []
    refused = installed.cli(fresh, "activate", env=env, code=2)
    assert refused["code"] == conflict
    assert status["next_steps"] == refused["next_steps"]
    if conflict == "custom_hooks_path":
        git(fresh, "config", "--unset", "core.hooksPath")
    else:
        assert hook.read_bytes() == foreign
        hook.rename(tmp_path / "preserved-pre-commit")
    unprepared = installed.cli(fresh, "status")
    assert unprepared["code"] == "needs_activation"
    assert unprepared["next_steps"][0] == "gateseal activate"
    assert installed.cli(fresh, "activate", env=env)["code"] == "prepared"
    assert installed.cli(fresh, "status")["code"] == "recorded"
    assert tracked(fresh) == before


def test_another_compilers_lock_is_named_before_any_fetch(installed, adopted, tmp_path):
    consumer, env = adopted
    fresh = tmp_path / "fresh"
    success(["git", "clone", consumer, fresh], tmp_path)
    lock = fresh / "gateseal.lock"
    document = json.loads(lock.read_bytes())
    compiler = document["compiler"]
    compiler["version"] = "0.1.1"
    compiler["filename"] = "gateseal-0.1.1-py3-none-any.whl"
    location = Path(compiler["source"]["location"])
    compiler["source"]["location"] = str(location.with_name(compiler["filename"]))
    lock.write_text(json.dumps(document))
    for command in ("status", "check", "activate"):
        refused = installed.cli(fresh, command, env=env, code=2)
        assert refused["code"] == "selected_compiler_required"
        assert "Use the locked gateseal 0.1.1 compiler." in json.dumps(refused)
    assert stored_policies(fresh) == []


def test_malformed_lock_is_named_before_its_policy_is_read(
    installed, adopted, tmp_path
):
    """Check the Lock's own fields before reading the release it names.

    A fresh clone has stored no release, so reading it first would report
    policy_unavailable instead of the malformed Lock.
    """
    consumer, env = adopted
    fresh = tmp_path / "fresh"
    success(["git", "clone", consumer, fresh], tmp_path)
    lock = fresh / "gateseal.lock"
    document = json.loads(lock.read_bytes())
    document["surprise"] = 1
    lock.write_text(json.dumps(document))
    refused = installed.cli(fresh, "status", env=env, code=2)
    assert refused["code"] == "invalid_field"
    assert stored_policies(fresh) == []


def test_damaged_store_is_reported_and_repaired(installed, adopted):
    consumer, env = adopted
    (entry,) = stored_policies(consumer)
    original = entry.read_bytes()
    entry.write_bytes(original.replace(b"trailing-whitespace", b"trailing-whitespacE"))
    try:
        status = installed.cli(consumer, "status", env=env)
        assert status["code"] == "needs_activation"
        assert "changed" in status["diagnostics"][0]["message"]
        (consumer / "notes.txt").write_text("damaged store\n")
        success(["git", "add", "notes.txt"], consumer)
        stopped = subprocess.run(
            ["git", "commit", "-m", "Commit"], cwd=consumer, capture_output=True
        )
        assert stopped.returncode != 0
        assert b"gateseal activate" in stopped.stdout + stopped.stderr
        assert installed.cli(consumer, "activate", env=env)["code"] == (
            "already_prepared"
        )
        assert entry.read_bytes() == original
        success(["git", "commit", "-m", "Commit"], consumer)
    finally:
        entry.write_bytes(original)


def test_lock_digest_must_be_its_commits_content(installed, adopted):
    consumer, env = adopted
    lock = consumer / "gateseal.lock"
    original = lock.read_bytes()
    document = json.loads(original)
    document["policy"]["digest"] = "sha256:" + "0" * 64
    lock.write_text(json.dumps(document))
    try:
        mismatch = installed.cli(consumer, "check", env=env, code=2)
        assert mismatch["code"] == "lock_mismatch"
        assert mismatch["next_steps"] == ["gateseal configure"]
        assert installed.cli(consumer, "configure", env=env)["changed"] == [lock.name]
        assert lock.read_bytes() == original
    finally:
        lock.write_bytes(original)


def test_bundled_consumer_adopts_by_editing_its_manifest(
    installed, team, configured, consumer
):
    origin, first, _ = team
    env = environment(installed, origin)
    manifest = consumer / "gateseal.toml"
    text = manifest.read_text()
    start, end = text.index("[policy]"), text.index("[choices]")
    manifest.write_text(
        text[:start]
        + f'[policy]\nsource = "git"\nrepository = "{URL}"\npath = "policy"\n'
        + f'commit = "{first}"\ntag = "v1"\n\n'
        + text[end:]
    )
    result = installed.cli(consumer, "configure", env=env)
    assert result["changed"] == ["gateseal.lock"]
    assert "policy_tag" in categories(result)
    assert json.loads((consumer / "gateseal.lock").read_bytes())["policy"]["kind"] == (
        "git"
    )


@pytest.mark.parametrize(
    "change,category",
    [
        (lambda data: (data / "policy.lock").chmod(0o755), "unsafe_policy_entry"),
        (
            lambda data: (
                (data / "rules" / "betterleaks.toml").unlink(),
                (data / "rules" / "betterleaks.toml").symlink_to("../policy.toml"),
            ),
            "unsafe_policy_entry",
        ),
        (
            lambda data: (data / "policy.toml").write_text(
                (data / "policy.toml").read_text().replace("schema = 4", "schema = 3")
            ),
            "unsupported_schema",
        ),
        (
            lambda data: (data / "policy.toml").write_text(
                'extends = "base.toml"\n' + (data / "policy.toml").read_text()
            ),
            "policy_inheritance",
        ),
        (
            lambda data: (data / "rules" / "betterleaks.toml").unlink(),
            "missing_policy_asset",
        ),
    ],
    ids=["executable", "symlink", "schema", "inheritance", "missing asset"],
)
def test_unsafe_or_incompatible_releases_write_nothing(
    installed, team, tmp_path, change, category
):
    origin, _, _ = team
    tag = f"bad-{tmp_path.name}"
    publish(origin, tag, change)
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    env = environment(installed, origin)
    refused = adopt(installed, consumer, env, tag, code=2)
    assert refused["code"] == category
    assert tracked(consumer) == {}
    assert stored_policies(consumer) == []


def test_policy_bytes_ignore_line_ending_conversion(installed, team, tmp_path):
    """Read a CRLF blob as the same content on every machine.

    The caller's autocrlf and the repository's attributes change nothing.
    """
    origin, _, _ = team

    def crlf(data):
        (data.parent / ".gitattributes").write_text("policy/*.toml -text\n")
        policy = data / "policy.toml"
        policy.write_bytes(policy.read_bytes().replace(b"\n", b"\r\n"))

    tag = "crlf"
    publish(origin, tag, crlf)
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    env = environment(installed, origin, **{"core.autocrlf": "true"})
    adopt(installed, consumer, env, tag)
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["policy"]["digest"] != POLICY.content_digest
    (entry,) = stored_policies(consumer)
    assert f'release = "{POLICY.release}"\r\n'.encode() in entry.read_bytes()


@pytest.mark.parametrize(
    "flags,category",
    [
        (
            [
                "--policy",
                "https://user:hunter2@policy.example/x.git",
                "--policy-tag",
                "v1",
            ],
            "invalid_policy_source",
        ),
        (["--policy", URL], "invalid_invocation"),
        (["--policy", URL, "--policy-tag", "v9"], "policy_tag_missing"),
        (
            ["--policy", URL, "--policy-tag", "v1", "--policy-path", "missing"],
            "policy_path_missing",
        ),
    ],
    ids=["credential", "no tag", "missing tag", "missing path"],
)
def test_invalid_git_selections_write_nothing(
    installed, team, consumer, flags, category
):
    env = environment(installed, team[0])
    refused = configure(
        installed, consumer, env, "--capability", "hygiene", *flags, code=2
    )
    assert refused["code"] == category
    assert "hunter2" not in json.dumps(refused)
    assert tracked(consumer) == {}


def test_unreachable_policy_fails_promptly_without_prompting(installed, consumer):
    env = dict(
        installed.environment, GIT_ASKPASS="/bin/false", SSH_ASKPASS="/bin/false"
    )
    env.pop("GIT_CONFIG_COUNT", None)
    refused = configure(
        installed,
        consumer,
        env,
        "--capability",
        "hygiene",
        "--policy",
        "https://127.0.0.1:9/team/policy.git",
        "--policy-tag",
        "v1",
        code=4,
    )
    assert refused["code"] == "policy_fetch_failed"
    message = refused["diagnostics"][0]["message"]
    assert message.startswith("Git could not read tag v1 of the policy repository")
    assert "0" * 40 not in message
    assert any("git ls-remote" in step for step in refused["next_steps"])
    assert tracked(consumer) == {}
    assert not [
        p for p in git_path(consumer, "gateseal").iterdir() if p.suffix == ".git"
    ]


def test_sha1_policy_is_read_where_git_defaults_to_sha256(team, monkeypatch, tmp_path):
    origin, first, _ = team
    monkeypatch.setenv("GIT_DEFAULT_HASH", "sha256")
    config = {
        f"url.{origin.as_uri()}.insteadOf": URL,
        "init.defaultObjectFormat": "sha256",
    }
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(config)))
    for index, (key, value) in enumerate(config.items()):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
    source = GitSource(URL, "policy", first, "v1")
    with sources.remote("git", tmp_path, URL) as remote:
        assert first in remote.verify_tag(source)
        assert remote.fetch(source).content_digest == POLICY.content_digest


def test_scratch_repository_ignores_git_config_redirect(team, monkeypatch, tmp_path):
    origin, first, _ = team
    redirect = tmp_path / "consumer.gitconfig"
    redirect.write_text('[remote "origin"]\n\turl = https://example.invalid/x.git\n')
    before = redirect.read_bytes()
    monkeypatch.setenv("GIT_CONFIG", str(redirect))
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", f"url.{origin.as_uri()}.insteadOf")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", URL)
    source = GitSource(URL, "policy", first, "v1")
    with sources.remote("git", tmp_path, URL) as remote:
        assert remote.fetch(source).content_digest == POLICY.content_digest
    assert redirect.read_bytes() == before


def test_store_refuses_a_symlinked_directory(tmp_path):
    from gateseal.diagnostics import GatesealError

    preparation, outside = tmp_path.resolve() / "gateseal", tmp_path.resolve() / "out"
    preparation.mkdir()
    outside.mkdir()
    unrelated = outside / ".gateseal-unrelated"
    unrelated.write_text("kept\n")
    (preparation / "policies").symlink_to(outside)
    with pytest.raises(GatesealError) as failure:
        sources.store(preparation, GitSource(URL, ".", "0" * 40, "v1"), POLICY)
    assert failure.value.code == "unsafe_path"
    assert sorted(p.name for p in outside.iterdir()) == [unrelated.name]


def test_interrupted_fetch_stops_git(monkeypatch, tmp_path):
    started = tmp_path / "started"
    git = tmp_path / "git"
    git.write_text(f'#!/bin/sh\necho $$ > "{started}"\nexec sleep 60\n')
    git.chmod(0o755)
    communicate = subprocess.Popen.communicate

    def interrupted(process, input=None, timeout=None):
        if timeout is None:
            return communicate(process, input)
        for _ in range(200):
            if started.exists() and started.read_text().strip():
                break
            time.sleep(0.05)
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupted)
    remote = sources.Remote(str(git), tmp_path, URL)
    with pytest.raises(KeyboardInterrupt):
        remote.run("ls-remote")
    with pytest.raises(ProcessLookupError):
        os.kill(int(started.read_text()), 0)


FIXTURES = "https://github.com/karstenevers/gateseal-fixtures.git"


@pytest.mark.network
def test_private_policy_fetch_uses_a_url_scoped_credential(monkeypatch, tmp_path):
    """Fetch a private policy with a token scoped to the policy URL.

    The fixture repository's commit that artifact-source.json pins holds a copy
    of the bundled policy. Only a credential helper scoped to the policy URL
    hands the token to Git; a helper for another URL gives nothing, and
    gateseal stores no credential.
    """
    from gateseal.diagnostics import GatesealError

    token = os.environ.get("GATESEAL_POLICY_TOKEN")
    if not token:
        pytest.skip("GATESEAL_POLICY_TOKEN is not set")
    fixture = json.loads((Path(__file__).parent / "artifact-source.json").read_bytes())
    commit = fixture["url"].split("/")[5]
    source = GitSource(FIXTURES, "policy", commit, "-")
    helper = (
        '!f() { test "$1" = get && echo username=x-access-token '
        '&& echo "password=$GATESEAL_POLICY_TOKEN"; }; f'
    )
    monkeypatch.delenv("GIT_ASKPASS", raising=False)
    for scope, works in (
        (FIXTURES, True),
        (FIXTURES.replace("gateseal-fixtures", "other"), False),
    ):
        config = {
            "credential.helper": "",
            f"credential.{scope}.helper": helper,
            "credential.useHttpPath": "true",
        }
        monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(config)))
        for index, (key, value) in enumerate(config.items()):
            monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
            monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)
        with sources.remote("git", tmp_path, FIXTURES) as origin:
            if works:
                assert origin.fetch(source).content_digest == POLICY.content_digest
            else:
                with pytest.raises(GatesealError) as failure:
                    origin.fetch(source)
                assert token not in str(failure.value)
