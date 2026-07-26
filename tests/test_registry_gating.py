"""Capability gating: entities appear only when the model supports the feature.

This is the safety property behind requirement "don't show a control the appliance can't
honour". It exercises the real dishwasher registry against synthetic capability profiles,
including the awkward no-profile case (unknown model → hide everything gated).
"""

from custom_components.holabrain.aiodollin import Device, parse_capability
from custom_components.holabrain.helpers import iter_specs

_DEVICE = Device(
    thing_code="t1", name="Dishwasher", device_type="0xE1", model="760EY179", online=True
)


class _FakeCoordinator:
    def __init__(self, profile):
        self.devices = {_DEVICE.thing_code: _DEVICE}
        self._profile = profile

    def capability_for(self, thing_code):
        return self._profile


def _keys(profile, attribute):
    coordinator = _FakeCoordinator(profile)
    return {spec.key for _, spec in iter_specs(coordinator, attribute)}


def test_full_profile_exposes_all_gated_entities():
    profile = parse_capability(
        [
            "rinse_aid", {"rinse_aid_gear": "5"}, "salt", {"salt_gear": "6"},
            "statistics", "auto_open",
        ]
    )
    assert {"doorstatus", "salt", "brightenAgent"} <= _keys(profile, "binary_sensors")
    assert {"runState", "autoDoorOpen"} <= _keys(profile, "switches")
    assert {"distributorGear", "softWaterGear"} == _keys(profile, "numbers")


def test_missing_salt_hides_salt_entities_only():
    profile = parse_capability(["rinse_aid", {"rinse_aid_gear": "5"}, "auto_open"])
    assert "salt" not in _keys(profile, "binary_sensors")  # salt-low hidden
    assert "softWaterGear" not in _keys(profile, "numbers")  # softener gated by salt
    assert "distributorGear" in _keys(profile, "numbers")  # rinse-aid still there
    assert "brightenAgent" in _keys(profile, "binary_sensors")


def test_missing_auto_open_hides_that_switch_but_keeps_running():
    profile = parse_capability(["rinse_aid", "salt"])
    switches = _keys(profile, "switches")
    assert "autoDoorOpen" not in switches
    assert "runState" in switches  # ungated control always present


def test_unknown_model_without_profile_hides_all_gated_entities():
    # No capability profile at all: keep only ungated entities.
    assert _keys(None, "binary_sensors") == {"doorstatus"}
    assert _keys(None, "switches") == {"power", "runState"}  # both ungated
    assert _keys(None, "numbers") == set()


def test_ungated_sensors_always_present():
    # Stage / program / fault / remaining / temperature are never gated.
    keys = _keys(None, "sensors")
    assert {"washingState", "modeEU", "faultCode", "remainTimeL", "realTemp"} <= keys
    # Statistics counters ARE gated and must be absent without the capability.
    assert "totalWaterVol" not in keys
