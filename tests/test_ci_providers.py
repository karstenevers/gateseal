"""Every CI provider's file meets one contract, whatever its syntax.

A provider that PROVIDERS gains runs every test here, and fails the first one
until gate_files can read its file.
"""

import contextlib
import dataclasses
import io
import json

import pytest
from conftest import PIN
from gate_files import SERVED, credential, gate_file, manifest
from gateseal.ci_providers import PROVIDERS
from gateseal.ci_providers.model import MISE_SHA256, MISE_URL, UV_SHA256, UV_URL
from gateseal.cli import main
from gateseal.diagnostics import GatesealError
from gateseal.manifest import parse_manifest
from gateseal.setup import PYTHON
from installation import clone, process

EACH = pytest.mark.parametrize("provider", sorted(PROVIDERS))

TEMPORARY = {"azure": "AGENT_TEMPDIRECTORY", "github": "RUNNER_TEMP"}
EVENT = {"azure": "GATESEAL_REASON", "github": "GATESEAL_EVENT"}


def test_every_provider_has_a_policy_url_and_its_variables():
    assert set(SERVED) == set(TEMPORARY) == set(EVENT) == set(PROVIDERS)


@EACH
def test_the_file_is_yaml_that_passes_file_hygiene(provider):
    file = gate_file(provider, SERVED[provider])
    assert isinstance(file.document, dict)
    assert file.data.endswith(b"\n") and not file.data.endswith(b"\n\n")
    assert all(line == line.rstrip() for line in file.data.decode().splitlines())
    assert b"\t" not in file.data


@EACH
def test_preparation_pins_its_tools_and_the_locked_gateseal(provider):
    env = gate_file(provider).prepare.env
    assert (env["GATESEAL_UV_URL"], env["GATESEAL_UV_SHA256"]) == (UV_URL, UV_SHA256)
    assert (env["GATESEAL_MISE_URL"], env["GATESEAL_MISE_SHA256"]) == (
        MISE_URL,
        MISE_SHA256,
    )
    assert (env["GATESEAL_REPOSITORY"], env["GATESEAL_COMMIT"]) == (
        PIN.repository,
        PIN.commit,
    )
    assert env["GATESEAL_PYTHON"] == PYTHON


@EACH
def test_preparation_checks_each_download_before_it_runs_one(provider):
    script = gate_file(provider).prepare.script
    for tool, name in (("UV", "uv.tar.gz"), ("MISE", "mise")):
        checked = f'echo "$GATESEAL_{tool}_SHA256  $tools/{name}" | sha256sum --check -'
        assert script.index(f'"$GATESEAL_{tool}_URL"') < script.index(checked)
        assert script.index(checked) < script.index("chmod 0755")
    assert script.splitlines()[-2:] == [
        '  "$command/bin/gateseal" setup',
        '"$command/bin/gateseal" check',
    ]
    assert '"gateseal @ git+$GATESEAL_REPOSITORY@$GATESEAL_COMMIT"' in script


@EACH
def test_a_lock_value_reaches_a_script_only_through_env(provider):
    other = dataclasses.replace(
        PIN, repository="https://example.invalid/fork/gateseal", commit="b" * 40
    )
    first = gate_file(provider, SERVED[provider])
    second = gate_file(provider, SERVED[provider] + "-2", branch="trunk", pin=other)
    assert (first.prepare.script, first.gate.script) == (
        second.prepare.script,
        second.gate.script,
    )
    scripts = second.prepare.script + second.gate.script
    values = (other.repository, other.commit, SERVED[provider], "trunk", UV_SHA256)
    assert not [value for value in values if value in scripts]
    assert other.commit in second.prepare.env.values()
    assert "$(" not in scripts and "${{" not in scripts


@EACH
def test_the_checkout_fetches_full_history_and_keeps_no_token(provider):
    checkout = gate_file(provider).checkout
    settings = checkout.get("with", checkout)
    depth = settings.get("fetch-depth", settings.get("fetchDepth"))
    kept = settings.get("persist-credentials", settings.get("persistCredentials"))
    assert depth == 0
    assert kept is False


@EACH
def test_only_its_own_https_policy_gets_a_token(provider):
    for repository in (
        None,
        *(url for name, url in SERVED.items() if name != provider),
        "https://gitlab.com/team/gateseal-policy.git",
        SERVED[provider].replace("https://", "ssh://git@"),
    ):
        file = gate_file(provider, repository)
        named = {
            key
            for key in file.prepare.env | file.gate.env
            if "TOKEN" in key or key.startswith("GIT_CONFIG")
        }
        assert not named, repository
    served = gate_file(provider, SERVED[provider])
    assert "GATESEAL_POLICY_TOKEN" in served.prepare.env
    assert not [key for key in served.gate.env if "TOKEN" in key]
    assert "NETRC" not in served.prepare.script + served.gate.script


@EACH
def test_the_token_goes_to_the_policy_repository_and_no_other(provider, tmp_path):
    step = gate_file(provider, SERVED[provider]).prepare
    tokens = {"GATESEAL_POLICY_TOKEN": "policy-token", "GATESEAL_JOB_TOKEN": "job"}
    assert credential(step, SERVED[provider], tokens, tmp_path) == "policy-token"
    for sibling in (
        SERVED[provider].replace("gateseal-policy", "other"),
        SERVED[provider].replace("gateseal-policy", "gateseal-policy-2"),
    ):
        assert credential(step, sibling, tokens, tmp_path) is None


@EACH
def test_an_event_the_file_does_not_handle_exits_2(provider, tmp_path):
    step = gate_file(provider).gate
    environment = {
        "PATH": "/usr/bin:/bin",
        TEMPORARY[provider]: str(tmp_path),
        EVENT[provider]: "SomethingNew",
    }
    ran = process(["bash", "-c", step.script], tmp_path, environment)
    assert ran.returncode == 2
    assert "gateseal: " in ran.stderr and "SomethingNew" in ran.stderr


def configure(root, *arguments):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = main(["--repo", str(root), "--json", "configure", *arguments])
    return code, json.loads(output.getvalue())


@EACH
def test_the_command_line_the_manifest_and_the_lock_know_the_provider(
    provider, tmp_path
):
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    path = PROVIDERS[provider].path
    code, result = configure(consumer, "--capability", "hygiene", "--ci", provider)
    assert code == 0, result
    assert path in result["changed"]
    assert parse_manifest((consumer / "gateseal.toml").read_bytes()).ci.provider == (
        provider
    )
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    owned = [name for name in lock["outputs"] if name in _paths()]
    assert owned == [path]


def _paths():
    return {provider.path for provider in PROVIDERS.values()}


def test_a_provider_that_no_writer_knows_is_refused_everywhere(tmp_path):
    consumer = clone(tmp_path / "origin", tmp_path / "consumer")
    code, result = configure(consumer, "--capability", "hygiene", "--ci", "gitlab")
    assert (code, result["code"]) == (2, "invalid_invocation")
    for name in sorted(PROVIDERS):
        assert name in result["diagnostics"][0]["message"]
    with pytest.raises(GatesealError) as failure:
        parse_manifest(manifest("gitlab"))
    assert failure.value.code == "unsupported_ci_provider"
    assert len(_paths()) == len(PROVIDERS)
