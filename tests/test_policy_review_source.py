"""A policy directory without policy.toml is refused by the file's name."""

import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy import LOCK, load_policy_tree


def test_a_policy_without_policy_toml_names_the_missing_file(tmp_path):
    (tmp_path / LOCK).write_text('{"components": {}}\n')
    with pytest.raises(GatesealError) as raised:
        load_policy_tree(tmp_path)
    assert raised.value.code == "missing_policy_asset"
    assert "policy.toml" in str(raised.value)
