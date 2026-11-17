import pytest
from gateseal.diagnostics import GatesealError
from gateseal.policy import load_bundled_policy, review_policy

BUNDLED = {asset.path: asset.content for asset in load_bundled_policy().assets}
SECRETS = "[capabilities.secrets.tools.betterleaks]\n"


@pytest.mark.parametrize("tool", ["Betterleaks", "better_leaks", "9leaks"])
def test_a_capability_names_its_tools_in_lowercase_letters_digits_and_dashes(tool):
    files = dict(BUNDLED)
    text = files["policy.toml"].decode()
    assert SECRETS in text
    table = f"[capabilities.secrets.tools.{tool}]\n"
    files["policy.toml"] = text.replace(SECRETS, table).encode()
    with pytest.raises(GatesealError) as raised:
        review_policy(files.__getitem__)
    assert raised.value.code == "invalid_field"
    named = (f"capabilities.secrets.tools.{tool}", "is not a tool name")
    assert all(value in str(raised.value) for value in named)
