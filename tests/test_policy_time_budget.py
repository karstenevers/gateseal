"""A policy may set the gate's time budget as a positive whole number of seconds."""

import pytest
from conftest import BUNDLED_MINIMUM as MINIMUM
from gateseal.policy import load_policy
from test_policy_review import assets, refused


def budget(value):
    return assets(MINIMUM, f"{MINIMUM}\ntime_budget_seconds = {value}")


@pytest.mark.parametrize("seconds", [1, 300])
def test_a_positive_whole_number_of_seconds_is_the_budget(seconds):
    assert load_policy(budget(seconds)).time_budget_seconds == seconds


def test_a_policy_without_the_field_has_no_budget():
    assert load_policy(assets(MINIMUM, MINIMUM)).time_budget_seconds is None


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "true", '"300"'])
def test_a_budget_that_is_not_a_positive_whole_number_is_refused(value):
    message = refused(budget(value))
    assert "time_budget_seconds" in message
    assert "300" in message
