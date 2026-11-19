"""The optional [ci] keys survive configure and upgrade with the installed command."""

import hashlib
import json

import pytest
import yaml
from gate_files import job, machine, pushes, timeout, weekly
from gateseal.ci_providers import PROVIDERS
from gateseal.validation import canonical
from test_ci_keys import RUNNER

pytestmark = pytest.mark.integration

EACH = pytest.mark.parametrize("provider", sorted(PROVIDERS))

DEFAULT = 'default_branch = "main"\n'
OLD_WEEKLY_RUN = {
    "azure": (
        '    include: ["main"]\n',
        'schedules:\n  - cron: "37 4 * * 1"\n    displayName: Weekly gateseal gate\n'
        '    branches:\n      include: ["main"]\n    always: true\n',
    ),
    "github": ("  workflow_dispatch:\n", '  schedule:\n    - cron: "37 4 * * 1"\n'),
}
"""The line after which a file of gateseal 0.2.0 has its weekly run, and the run."""
KEYS_CALLED = {"name", True, "permissions", "jobs"}
"""The top-level keys of a GitHub workflow that another workflow calls."""


def configure_ci(installed, root, provider):
    installed.cli(root, "configure", "--capability", "hygiene", "--ci", provider)
    return root / PROVIDERS[provider].path


def edit_ci(root, old, new):
    manifest = root / "gateseal.toml"
    text = manifest.read_text()
    assert old in text
    manifest.write_text(text.replace(old, new, 1))


def document(path):
    return yaml.safe_load(path.read_bytes())


def write_as_old_release(root, provider, path):
    """Give the file the weekly run that gateseal 0.2.0 wrote, and own it."""
    after, run = OLD_WEEKLY_RUN[provider]
    data = path.read_text().replace(after, after + run, 1).encode()
    path.write_bytes(data)
    lock_path = root / "gateseal.lock"
    lock = json.loads(lock_path.read_bytes())
    name = PROVIDERS[provider].path
    lock["outputs"][name]["sha256"] = hashlib.sha256(data).hexdigest()
    lock_path.write_bytes(canonical(lock))


@EACH
def test_an_upgrade_drops_the_old_weekly_run_until_the_manifest_asks(
    installed, next_installed, consumer, provider
):
    path = configure_ci(installed, consumer, provider)
    write_as_old_release(consumer, provider, path)
    assert weekly(provider, document(path)) is not None
    upgraded = next_installed.cli(consumer, "upgrade")
    assert PROVIDERS[provider].path in upgraded["changed"]
    assert weekly(provider, document(path)) is None
    assert next_installed.cli(consumer, "check")["code"] == "consistent"
    edit_ci(consumer, DEFAULT, DEFAULT + "schedule = true\n")
    next_installed.cli(consumer, "configure")
    assert weekly(provider, document(path))[0]["cron"] == "37 4 * * 1"
    assert next_installed.cli(consumer, "check")["code"] == "consistent"


def without_steps(provider, document):
    """Return document with its job's steps, which name the pinned release, left out."""
    kept = dict(job(provider, document))
    del kept["steps"]
    return {**document, "jobs": kept}


RELEASE = {"azure": "release/*", "github": "release/**"}
MODES = {
    "own": DEFAULT
    + 'branches = ["release/*"]\nschedule = true\ntimeout_minutes = 60\n',
    "included": "standalone = false\ntimeout_minutes = 60\n",
}


@EACH
@pytest.mark.parametrize("mode", sorted(MODES))
def test_configure_and_upgrade_keep_every_key(
    installed, next_installed, consumer, provider, mode
):
    path = configure_ci(installed, consumer, provider)
    keys, named = RUNNER[provider]
    edit_ci(consumer, DEFAULT, MODES[mode] + keys)
    installed.cli(consumer, "configure")
    manifest = (consumer / "gateseal.toml").read_bytes()
    configured = document(path)
    assert machine(provider, configured) == named
    assert timeout(provider, configured) == 60
    if mode == "own":
        assert pushes(provider, configured) == ["main", RELEASE[provider]]
        assert weekly(provider, configured) is not None
    again = installed.cli(consumer, "configure")
    assert PROVIDERS[provider].path not in again["changed"]
    assert (consumer / "gateseal.toml").read_bytes() == manifest
    next_installed.cli(consumer, "upgrade")
    assert (consumer / "gateseal.toml").read_bytes() == manifest
    assert without_steps(provider, document(path)) == without_steps(
        provider, configured
    )
    assert next_installed.cli(consumer, "check")["code"] == "consistent"


@EACH
def test_a_change_of_mode_rewrites_the_file(installed, consumer, provider):
    path = configure_ci(installed, consumer, provider)
    own = path.read_bytes()
    edit_ci(consumer, DEFAULT, "standalone = false\n")
    included = installed.cli(consumer, "configure")
    assert PROVIDERS[provider].path in included["changed"]
    assert set(document(path)) == {"azure": {"jobs"}, "github": KEYS_CALLED}[provider]
    assert installed.cli(consumer, "check")["code"] == "consistent"
    edit_ci(consumer, "standalone = false\n", DEFAULT)
    back = installed.cli(consumer, "configure")
    assert PROVIDERS[provider].path in back["changed"]
    assert path.read_bytes() == own
    assert installed.cli(consumer, "check")["code"] == "consistent"
