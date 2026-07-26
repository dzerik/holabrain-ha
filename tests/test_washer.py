"""Washing machine category: entity set and capability gating.

A washer has no native Home Assistant platform, so its usefulness depends entirely on the
descriptors being right. These tests exercise the real registry against capability profiles
that differ the way real models differ — a dryer-less machine, a machine without automatic
dosing — and assert on what the user would actually see.
"""

from __future__ import annotations

import pytest

from custom_components.holabrain.aiodollin import Device, parse_capability
from custom_components.holabrain.helpers import iter_specs
from custom_components.holabrain.registry import get_category

WASHER = Device(
    thing_code="db1",
    name="Washer",
    device_type="0xDB",
    model="38127413",
    online=True,
)

FULL = [
    "program_supported",
    "drying_supported",
    "speed_wash_supported",
    "auto_dose_supported",
    "temp_editable",
    "extra_rinse_editable",
]


class _FakeCoordinator:
    def __init__(self, profile) -> None:
        self.devices = {WASHER.thing_code: WASHER}
        self._profile = profile

    def capability_for(self, thing_code):
        return self._profile


def _keys(features: list[str] | None, attribute: str) -> set[str]:
    profile = None if features is None else parse_capability(features)
    return {spec.key for _, spec in iter_specs(_FakeCoordinator(profile), attribute)}


def test_the_category_is_registered_as_composite():
    category = get_category("0xDB")
    assert category is not None
    assert category.primary_platform is None  # no native washer platform in HA


def test_a_fully_featured_model_exposes_every_control():
    assert {"cycle", "temp", "speed", "dry"} == _keys(FULL, "selects")
    assert {"power", "startPause", "addRinse", "addSpeedWash", "autoDose"} == _keys(
        FULL, "switches"
    )
    assert {"detergentShortage", "softenerLiquidShortage"} <= _keys(FULL, "binary_sensors")


def test_a_machine_without_a_dryer_does_not_offer_a_drying_level():
    """Offering a drying level on a washer without a dryer produces a command it refuses."""
    without_dryer = [f for f in FULL if f != "drying_supported"]
    assert "dry" not in _keys(without_dryer, "selects")
    assert "cycle" in _keys(without_dryer, "selects")  # the rest is unaffected


def test_no_automatic_dosing_hides_both_the_switch_and_the_level_warnings():
    """The dosing switch and the two 'low' warnings share one capability and must agree.

    Showing "detergent low" on a machine that cannot measure detergent is worse than showing
    nothing: it looks like a real reading.
    """
    without_dosing = [f for f in FULL if f != "auto_dose_supported"]
    assert "autoDose" not in _keys(without_dosing, "switches")
    assert "detergentShortage" not in _keys(without_dosing, "binary_sensors")
    assert "softenerLiquidShortage" not in _keys(without_dosing, "binary_sensors")


def test_core_controls_survive_an_unknown_model():
    """With no profile at all the machine must still be usable: power, start/pause, status."""
    assert {"power", "startPause"} == _keys(None, "switches")
    assert {"runState", "washingStatus", "faultCode", "remainTime"} <= _keys(None, "sensors")
    # Everything optional is gated off rather than guessed.
    assert _keys(None, "selects") == {"speed"}


@pytest.mark.parametrize(
    ("missing", "hidden"),
    [
        ("temp_editable", "temp"),
        ("program_supported", "cycle"),
    ],
)
def test_each_optional_select_is_gated_by_its_own_capability(missing: str, hidden: str):
    features = [f for f in FULL if f != missing]
    assert hidden not in _keys(features, "selects")


def test_status_and_delay_are_always_available():
    """Remaining time, phase and delayed start are inherent to a washer, never gated."""
    assert "orderTime" in _keys(None, "numbers")
    assert "remainTime" in _keys(None, "sensors")
