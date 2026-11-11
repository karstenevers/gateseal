"""Read a policy repository through a scratch bare repository and the user's Git.

The policy repository is a real local repository that its canonical URL
reaches through url.<base>.insteadOf. Git's global configuration is a file of
each test's own, and the system configuration is off.
"""

import socket
import threading

import pytest
from gateseal import sources
from gateseal.diagnostics import GatesealError
from gateseal.manifest import GitSource
from team_policy import URL, git, publish


@pytest.fixture(scope="module")
def team(tmp_path_factory):
    """A policy repository with a good release v1 and an executable file in v2."""
    origin = tmp_path_factory.mktemp("team policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    first = publish(origin, "v1")
    second = publish(origin, "v2", lambda data: (data / "policy.toml").chmod(0o755))
    return origin, first, second


@pytest.fixture
def configure(tmp_path, monkeypatch):
    """Return a function that makes settings Git's whole configuration."""
    settings = tmp_path / "global.gitconfig"
    settings.touch()
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(settings))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_CONFIG_PARAMETERS", "GIT_SSH", "GIT_SSH_COMMAND"):
        monkeypatch.delenv(name, raising=False)

    def configured(settings):
        monkeypatch.setenv("GIT_CONFIG_COUNT", str(len(settings)))
        for index, (key, value) in enumerate(settings.items()):
            monkeypatch.setenv(f"GIT_CONFIG_KEY_{index}", key)
            monkeypatch.setenv(f"GIT_CONFIG_VALUE_{index}", value)

    configured({})
    return configured


@pytest.fixture
def preparation(tmp_path):
    directory = tmp_path / "gateseal"
    directory.mkdir()
    return directory


def refused(call, *arguments):
    with pytest.raises(GatesealError) as failure:
        call(*arguments)
    return failure.value


def test_git_that_requires_named_bare_repositories_reads_the_tag(
    team, configure, preparation
):
    origin, first, _ = team
    configure(
        {f"url.{origin.as_uri()}.insteadOf": URL, "safe.bareRepository": "explicit"}
    )
    with sources.remote("git", preparation, URL) as remote:
        assert remote.tagged("v1") == first
        assert remote.peeled("v9") is None
    assert list(preparation.iterdir()) == []


def test_an_executable_policy_file_is_refused(team, configure, preparation):
    origin, _, second = team
    configure({f"url.{origin.as_uri()}.insteadOf": URL})
    with sources.remote("git", preparation, URL) as remote:
        error = refused(remote.fetch, GitSource(URL, "policy", second, "v2"))
    assert error.code == "unsafe_policy_entry"
    assert "policy.toml" in str(error)


def test_a_policy_path_that_names_a_file_is_missing(team, configure, preparation):
    origin, first, _ = team
    configure({f"url.{origin.as_uri()}.insteadOf": URL})
    source = GitSource(URL, "policy/policy.toml", first, "v1")
    with sources.remote("git", preparation, URL) as remote:
        error = refused(remote.fetch, source)
    assert error.code == "policy_path_missing"
    assert all(value in str(error) for value in (first, "policy/policy.toml"))


def test_a_failed_read_names_what_it_read_and_where(configure, preparation, tmp_path):
    missing = str(tmp_path / "missing")
    with sources.remote("git", preparation, missing) as remote:
        error = refused(remote.peeled, "v1")
    assert (error.code, error.exit_code) == ("policy_fetch_failed", 4)
    assert all(value in str(error) for value in ("tag v1", missing))
    assert f"git ls-remote {missing}" in error.next_step


def test_a_git_that_cannot_start_is_unavailable(tmp_path):
    remote = sources.Remote(str(tmp_path / "no git"), tmp_path, URL)
    error = refused(remote.run, "ls-remote")
    assert (error.code, error.exit_code) == ("tool_unavailable", 4)


def test_transports_run_without_prompts(configure, preparation, tmp_path, monkeypatch):
    """A stand-in ssh records the environment that a real one would prompt from."""
    for name, value in (
        ("GIT_TERMINAL_PROMPT", "1"),
        ("SSH_ASKPASS_REQUIRE", "force"),
        ("GCM_INTERACTIVE", "always"),
    ):
        monkeypatch.setenv(name, value)
    recorded = tmp_path / "ssh environment"
    ssh = tmp_path / "ssh"
    ssh.write_text(f"#!/bin/sh\nenv > '{recorded}'\nexit 1\n")
    ssh.chmod(0o755)
    configure({"core.sshCommand": str(ssh), "ssh.variant": "simple"})
    repository = "ssh://policy.invalid/team/gateseal-policy.git"
    with sources.remote("git", preparation, repository) as remote:
        assert refused(remote.peeled, "v1").code == "policy_fetch_failed"
    seen = set(recorded.read_text().splitlines())
    assert {
        "GIT_TERMINAL_PROMPT=0",
        "SSH_ASKPASS_REQUIRE=never",
        "GCM_INTERACTIVE=never",
    } <= seen


def test_a_git_that_never_finishes_is_stopped(configure, preparation, monkeypatch):
    """A server that accepts and never answers keeps git waiting until stopped."""
    monkeypatch.setattr(sources, "TIMEOUT", 0.3)
    closed = threading.Event()
    with socket.create_server(("127.0.0.1", 0)) as server:

        def silent():
            connection, _ = server.accept()
            with connection:
                connection.settimeout(5)
                while connection.recv(4096):
                    pass
                closed.set()

        listener = threading.Thread(target=silent, daemon=True)
        listener.start()
        repository = f"git://127.0.0.1:{server.getsockname()[1]}/policy.git"
        with sources.remote("git", preparation, repository) as remote:
            error = refused(remote.peeled, "v1")
        assert closed.wait(2), "git still holds its connection"
    assert error.code == "policy_fetch_failed"
    assert "0.3 seconds" in str(error)
