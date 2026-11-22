"""A Repository Maintainer runs a whole-project script once instead of per file.

Each test configures a plain directory in process, as test_repository_hooks
does. The project hook's table comes from the Manifest reference.
"""

import json

import pytest
from doc_examples import example
from test_lock_configure import CONFIG
from test_repository_hooks import LICENSES, configure, refused, repository

GENERATOR = "\n" + example("policy-contract.md", "manifest tables: project hook")


def configured(tmp_path, *tables):
    """Configure a worktree whose Manifest holds tables.

    Return the generated local hooks and the notes about repository hooks.
    """
    root = repository(tmp_path, *tables)
    (root / "scripts/generate-api-types.sh").write_text("#!/bin/sh\n")
    _, notes = configure(root)
    *_, local = json.loads((root / CONFIG).read_bytes())["repos"]
    return local["hooks"], [n.message for n in notes if n.category == "repository_hook"]


def test_a_project_hook_with_files_gets_no_file_names_and_starts_on_a_match(
    tmp_path,
):
    hooks, notes = configured(tmp_path, GENERATOR)
    assert hooks == [
        {
            "id": "api-types",
            "name": "Check that the API types are fresh",
            "stages": ["pre-commit", "manual"],
            "always_run": False,
            "pass_filenames": False,
            "language": "unsupported_script",
            "entry": "scripts/generate-api-types.sh",
            "files": "^schema/",
        }
    ]
    assert notes == [
        "Repository hook api-types: scripts/generate-api-types.sh, once for the "
        "project when a checked file matches ^schema/."
    ]


def test_a_project_hook_without_files_starts_at_every_run(tmp_path):
    hooks, notes = configured(tmp_path, LICENSES + 'scope = "project"\n')
    assert hooks == [
        {
            "id": "check-licenses",
            "name": "Check licenses",
            "stages": ["pre-commit", "manual"],
            "always_run": True,
            "pass_filenames": False,
            "language": "unsupported_script",
            "entry": "bin/licenses",
        }
    ]
    assert notes == [
        "Repository hook check-licenses: bin/licenses, once for the project at "
        "every run."
    ]


def test_a_project_hook_keeps_its_args_and_stages(tmp_path):
    table = LICENSES + 'scope = "project"\nargs = ["--all"]\nstages = ["ci"]\n'
    (hook,) = configured(tmp_path, table)[0]
    assert (hook["entry"], hook["stages"]) == ("bin/licenses --all", ["manual"])


def test_the_files_scope_compiles_to_the_same_bytes_as_no_scope(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "listed").mkdir()
    plain = repository(tmp_path / "plain", LICENSES)
    listed = repository(tmp_path / "listed", LICENSES + 'scope = "files"\n')
    configure(plain)
    configure(listed)
    assert (plain / CONFIG).read_bytes() == (listed / CONFIG).read_bytes()


@pytest.mark.parametrize("value", ['["project"]', "1", '""'])
def test_scope_is_a_name(tmp_path, value):
    root = repository(tmp_path, LICENSES + f"scope = {value}\n")
    assert "hooks.check-licenses.scope" in str(refused(root, "invalid_field"))


@pytest.mark.parametrize("value", ['"Project"', '"whole"'])
def test_an_unknown_scope_names_both_values(tmp_path, value):
    root = repository(tmp_path, LICENSES + f"scope = {value}\n")
    assert str(refused(root, "invalid_field")) == (
        'hooks.check-licenses.scope must be "files" or "project".'
    )
