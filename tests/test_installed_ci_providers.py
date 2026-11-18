"""Configure each CI provider with the installed command, and switch between them."""

import json

import pytest
import yaml
from conftest import preserved, with_git_config
from gateseal.ci_providers import PROVIDERS
from team_policy import git, publish

pytestmark = pytest.mark.integration

AZURE = PROVIDERS["azure"].path
GITHUB = PROVIDERS["github"].path


def configure(installed, root, *arguments, code=0):
    return installed.cli(root, "configure", *arguments, code=code)


def owned(root):
    outputs = json.loads((root / "gateseal.lock").read_bytes())["outputs"]
    return {name for name in outputs if name in (AZURE, GITHUB)}


def select(root, old, new):
    manifest = root / "gateseal.toml"
    text = manifest.read_text()
    assert f'provider = "{old}"' in text
    manifest.write_text(text.replace(f'provider = "{old}"', f'provider = "{new}"'))


def test_configure_owns_the_pipeline_and_check_guards_it(installed, consumer):
    result = configure(installed, consumer, "--capability", "hygiene", "--ci", "azure")
    assert AZURE in result["changed"]
    assert '[ci]\nprovider = "azure"\n' in (consumer / "gateseal.toml").read_text()
    assert owned(consumer) == {AZURE}
    lock = json.loads((consumer / "gateseal.lock").read_bytes())
    assert lock["gateseal"]["commit"] in (consumer / AZURE).read_text()
    assert not (consumer / GITHUB).exists()
    assert installed.cli(consumer, "check")["code"] == "consistent"
    with preserved(consumer / AZURE) as original:
        (consumer / AZURE).write_bytes(original.replace(b"ubuntu-24.04", b"ubuntu"))
        edited = installed.cli(consumer, "check", code=2)
        assert edited["code"] == "ownership_conflict"
        assert AZURE in json.dumps(edited)
    refused = configure(installed, consumer, "--ci", "github", code=2)
    assert refused["code"] == "configure_assertion_failed"
    assert "azure" in refused["diagnostics"][0]["message"]


@pytest.mark.parametrize(
    "old,new,left,written",
    [("github", "azure", GITHUB, AZURE), ("azure", "github", AZURE, GITHUB)],
)
def test_a_switch_of_provider_replaces_the_unedited_file(
    installed, consumer, old, new, left, written
):
    configure(installed, consumer, "--capability", "hygiene", "--ci", old)
    select(consumer, old, new)
    with preserved(consumer / left) as original:
        (consumer / left).write_bytes(original + b"# kept\n")
        kept = configure(installed, consumer, code=2)
        assert kept["code"] == "ownership_conflict"
        assert left in kept["diagnostics"][0]["message"]
        assert (consumer / left).exists() and not (consumer / written).exists()
    switched = configure(installed, consumer)
    assert {left, written} <= set(switched["changed"])
    assert not (consumer / left).exists()
    assert owned(consumer) == {written}
    assert installed.cli(consumer, "check")["code"] == "consistent"


def test_the_pipeline_names_a_team_policy_in_azure_repos(installed, consumer, tmp_path):
    url = "https://dev.azure.com/team/Platform%20Team/_git/gateseal-policy"
    origin = tmp_path / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1")
    rewrite = {f"url.{origin.as_uri()}.insteadOf": url}
    env = with_git_config(installed.environment, rewrite)
    policy = ("--policy", url, "--policy-path", "policy", "--policy-tag", "v1")
    installed.cli(
        consumer,
        "configure",
        "--capability",
        "hygiene",
        "--ci",
        "azure",
        *policy,
        env=env,
    )
    document = yaml.safe_load((consumer / AZURE).read_bytes())
    [resource] = document["resources"]["repositories"]
    assert resource["name"] == "Platform Team/gateseal-policy"
    [job] = document["jobs"]
    assert job["uses"] == {"repositories": [resource["repository"]]}
    assert installed.cli(consumer, "check", env=env)["code"] == "consistent"
