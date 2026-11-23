"""A team definition installs a project's extra and dependency group.

Release v1 of a team policy runs the how-to's mypy with the project's extra
net and its group typing. Release v2 runs it with neither. The project's
labels.py imports idna from the extra and platformdirs from the group.
"""

import pytest
from conftest import on_machine
from installation import clone, process, success
from mypy_policy import add_mypy
from team_policy import URL, environment, git, publish
from uv_projects import CHECKOUT, PRICING, PYPROJECT, write_files

pytestmark = pytest.mark.integration

PROJECT = {
    "pyproject.toml": PYPROJECT
    + '\n[project.optional-dependencies]\nnet = ["idna==3.11"]\n'
    + '\n[dependency-groups]\ntyping = ["platformdirs==4.3.6"]\n',
    "shop/__init__.py": "",
    "shop/pricing.py": PRICING,
    "checkout.py": CHECKOUT,
    "labels.py": """\
import idna
import platformdirs

print(idna.encode("example.com"), platformdirs.user_cache_dir("shop"))
""",
}
MISSING = (
    "labels.py:{}: error: Cannot find implementation or library stub for module "
    'named "{}"'
)


def extra_and_group(data):
    """Offer the how-to's mypy with the project's extra net and group typing."""
    add_mypy(data)
    path = data / "tools/mypy.toml"
    source = 'package = "mypy"\n'
    path.write_text(
        path.read_text().replace(
            source, f'{source}extras = ["net"]\ngroups = ["typing"]\n'
        )
    )


@pytest.fixture(scope="module")
def origin(tmp_path_factory):
    """The team policy's repository: v1 names the extra and group, v2 neither."""
    origin = tmp_path_factory.mktemp("mypy extras policy") / "gateseal-policy"
    origin.mkdir()
    git(origin, "init", "--initial-branch=main")
    publish(origin, "v1", extra_and_group)
    publish(origin, "v2", add_mypy)
    return origin


def commit(root, env, message):
    success(["git", "add", "-A"], root)
    result = process(["git", "commit", "-m", message], root, env)
    return result, result.stdout + result.stderr


def test_the_tool_installs_only_the_extras_and_groups_its_release_names(
    installed, origin, tmp_path
):
    env = environment(installed, origin)
    source = ("--policy", URL, "--policy-path", "policy", "--policy-tag", "v1")
    with on_machine(installed):
        consumer = clone(tmp_path / "origin", tmp_path / "consumer")
        write_files(consumer, PROJECT)
        installed.cli(
            consumer, "configure", "--capability", "python-types", *source, env=env
        )
        result, output = commit(consumer, env, "Check types with mypy")
        assert result.returncode == 0, output
        second = tmp_path / "new clone"
        success(["git", "clone", "--quiet", consumer, second], tmp_path)
    for setting in ("user.name=gateseal test", "user.email=gateseal@example.invalid"):
        success(["git", "config", *setting.split("=")], second)
    upgrade = ("upgrade", "--policy-tag", "v2")
    assert installed.cli(second, *upgrade, env=env)["code"] == "upgraded"
    result, output = commit(second, env, "Upgrade the policy")
    assert result.returncode == 1, output
    assert MISSING.format(1, "idna") in output
    assert MISSING.format(2, "platformdirs") in output
