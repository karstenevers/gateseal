"""A team's Git policy repository that tests publish releases into.

The canonical https URL reaches a real local repository through
url.<base>.insteadOf, as a laptop's Git configuration would map it, so
gateseal stores only the credential-free URL.
"""

import shutil

from conftest import with_git_config
from gateseal.policy import load_bundled_policy
from installation import success

URL = "https://policy.example/team/gateseal-policy.git"
IDENTITY = ["-c", "user.name=Policy author", "-c", "user.email=policy@example.invalid"]
POLICY = load_bundled_policy()


def git(root, *arguments):
    return success(["git", *IDENTITY, *arguments], root).stdout.strip()


def publish(origin, tag, change=None, annotated=True):
    """Commit origin's policy/ as the bundled release, changed, and tag it."""
    data = origin / "policy"
    shutil.rmtree(data, ignore_errors=True)
    data.mkdir()
    for asset in POLICY.assets:
        (data / asset.path).parent.mkdir(exist_ok=True)
        (data / asset.path).write_bytes(asset.content)
    if change:
        change(data)
    git(origin, "add", "-A")
    git(origin, "commit", "--allow-empty", "-m", f"Release {tag}")
    git(origin, "tag", *(["-a", "-m", tag] if annotated else []), tag)
    return git(origin, "rev-parse", "HEAD")


def environment(installed, origin, **settings):
    """Return the environment whose Git maps the team URL to origin."""
    config = {f"url.{origin.as_uri()}.insteadOf": URL, **settings}
    return with_git_config(installed.environment, config)
