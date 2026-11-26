"""A Node project's package manager is the one whose lockfile it commits."""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.tools.package_managers import NPM, PNPM, package_manager

NEEDS = "tsc runs in the repository's Node project, but"
INSTEAD = (
    ", or stop selecting typescript-types in gateseal.toml. Then run gateseal configure"
)


def project(root, *names):
    for name in names:
        (root / name).write_text("{}")
    return root


def refused(root, instead):
    with pytest.raises(GatesealError) as raised:
        package_manager(root, NEEDS, instead)
    return raised.value


@pytest.mark.parametrize(
    ("lockfile", "expected"), [("package-lock.json", NPM), ("pnpm-lock.yaml", PNPM)]
)
def test_the_committed_lockfile_names_the_package_manager(tmp_path, lockfile, expected):
    root = project(tmp_path, "package.json", lockfile)
    assert package_manager(root, NEEDS) is expected


def test_a_folder_named_like_a_lockfile_is_no_lockfile(tmp_path):
    root = project(tmp_path, "package.json", "package-lock.json")
    (root / "pnpm-lock.yaml").mkdir()
    assert package_manager(root, NEEDS) is NPM


@pytest.mark.parametrize("instead", ["", INSTEAD])
def test_a_repository_without_package_json_is_no_node_project(tmp_path, instead):
    root = project(tmp_path, "package-lock.json")
    failure = refused(root, instead)
    assert failure.code == "missing_project"
    assert str(failure) == (
        "tsc runs in the repository's Node project, but the repository has no "
        "package.json. Only npm and pnpm projects are supported."
    )
    assert failure.next_step == (
        f"Create the project's package.json with npm or pnpm and commit it{instead}."
    )


def test_a_folder_named_package_json_is_no_node_project(tmp_path):
    root = project(tmp_path, "pnpm-lock.yaml")
    (root / "package.json").mkdir()
    assert refused(root, "").code == "missing_project"


@pytest.mark.parametrize("instead", ["", INSTEAD])
def test_a_node_project_needs_a_lockfile(tmp_path, instead):
    root = project(tmp_path, "package.json")
    (root / "package-lock.json").mkdir()
    failure = refused(root, instead)
    assert failure.code == "missing_project"
    assert str(failure) == (
        "tsc runs in the repository's Node project, but the repository has neither "
        "package-lock.json nor pnpm-lock.yaml. Only npm and pnpm projects are "
        "supported."
    )
    assert failure.next_step == (
        f"Run npm install or pnpm install and commit the lockfile{instead}."
    )


@pytest.mark.parametrize("instead", ["", INSTEAD])
def test_two_lockfiles_leave_the_package_manager_unclear(tmp_path, instead):
    root = project(tmp_path, "package.json", "package-lock.json", "pnpm-lock.yaml")
    failure = refused(root, instead)
    assert failure.code == "ambiguous_project"
    assert str(failure) == (
        "tsc runs in the repository's Node project, but the repository has both "
        "package-lock.json and pnpm-lock.yaml, so its package manager is not clear."
    )
    assert failure.next_step == (
        "Delete the lockfile that the project does not use and commit the "
        f"change{instead}."
    )


@pytest.mark.parametrize("instead", ["", INSTEAD])
def test_an_npm_shrinkwrap_beside_the_lockfile_leaves_the_pins_unclear(
    tmp_path, instead
):
    root = project(tmp_path, "package.json", "package-lock.json", "npm-shrinkwrap.json")
    failure = refused(root, instead)
    assert failure.code == "ambiguous_project"
    assert str(failure) == (
        "tsc runs in the repository's Node project, but the repository has both "
        "package-lock.json and npm-shrinkwrap.json, and npm reads "
        "npm-shrinkwrap.json in place of package-lock.json."
    )
    assert failure.next_step == (
        f"Delete npm-shrinkwrap.json and commit the change{instead}."
    )


def test_a_folder_named_like_an_npm_shrinkwrap_is_no_shrinkwrap(tmp_path):
    root = project(tmp_path, "package.json", "package-lock.json")
    (root / "npm-shrinkwrap.json").mkdir()
    assert package_manager(root, NEEDS) is NPM


def test_a_pnpm_project_ignores_an_npm_shrinkwrap(tmp_path):
    root = project(tmp_path, "package.json", "pnpm-lock.yaml", "npm-shrinkwrap.json")
    assert package_manager(root, NEEDS) is PNPM


def test_an_npm_shrinkwrap_without_a_lockfile_is_no_node_project(tmp_path):
    root = project(tmp_path, "package.json", "npm-shrinkwrap.json")
    assert refused(root, "").code == "missing_project"


def test_instead_defaults_to_no_other_step(tmp_path):
    with pytest.raises(GatesealError) as raised:
        package_manager(project(tmp_path, "package.json"), NEEDS)
    assert raised.value.next_step.endswith("commit the lockfile.")
