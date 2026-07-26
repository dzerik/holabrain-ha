"""Capability parsing tests, driven by a real ``function/dict`` payload.

The whole point of the capability profile is to *hide* features a model does not have, so
these tests assert both presence and absence, and cover the awkward encodings the cloud
actually uses (numeric gears as strings, the split mode/modelValue arrays).
"""

from custom_components.holabrain.aiodollin.dto.capability import parse_capability

# Exact shape returned by the cloud for a real dishwasher model (760EY179).
_DISHWASHER = [
    "rinse_aid",
    {"rinse_aid_gear": "5"},
    "salt",
    {"salt_gear": "6"},
    "statistics",
    "delay",
    {
        "mode": ["auto", "intensive", "universal", "eco", "delicate", "90mins", "rapid", "soak"],
        "modelValue": [
            {"auto": "1"},
            {"intensive": "2"},
            {"universal": "3"},
            {"eco": "4"},
            {"delicate": "5"},
            {"90mins": "6"},
            {"rapid": "7"},
            {"soak": "8"},
        ],
    },
    {"alternate_wash": ["upper", "lower", "upper_and_lower"]},
    "power_wash",
    "extra_drying",
    "turbo_speed",
]


def test_supported_features_present():
    profile = parse_capability(_DISHWASHER, model="760EY179")
    for feature in ("rinse_aid", "salt", "delay", "power_wash", "extra_drying", "turbo_speed"):
        assert profile.supports(feature), feature


def test_unsupported_feature_is_hidden():
    # This model has no UV lamp and no soak-only drying. Absence must read as False so the
    # adapter never creates entities the appliance can't honour.
    profile = parse_capability(_DISHWASHER, model="760EY179")
    assert not profile.supports("uv")
    assert not profile.supports("air_dry")


def test_extra_drying_gating_differs_between_models():
    # A stripped model without extra_drying must report it as unsupported — this is exactly
    # the "don't show forced drying if the model lacks it" requirement.
    without = [m for m in _DISHWASHER if m != "extra_drying"]
    assert parse_capability(_DISHWASHER).supports("extra_drying")
    assert not parse_capability(without).supports("extra_drying")


def test_gear_maxima_are_numeric():
    profile = parse_capability(_DISHWASHER)
    assert profile.gear_max("rinse_aid") == 5
    assert profile.gear_max("salt") == 6
    # A feature without a gear must not fabricate one.
    assert profile.gear_max("power_wash") is None


def test_mode_code_map_folds_parallel_arrays():
    profile = parse_capability(_DISHWASHER)
    codes = profile.mode_codes()
    assert codes["auto"] == "1"
    assert codes["soak"] == "8"
    assert len(codes) == 8


def test_alternate_wash_options_preserved_in_order():
    profile = parse_capability(_DISHWASHER)
    assert profile.option_values("alternate_wash") == ["upper", "lower", "upper_and_lower"]


def test_empty_capability_list_is_safe():
    profile = parse_capability([], model="unknown")
    assert profile.features == frozenset()
    assert profile.mode_codes() == {}
    assert profile.gear_max("rinse_aid") is None


def test_non_numeric_gear_is_ignored_not_crashed():
    # Defensive: a malformed gear value must not raise and must not masquerade as a number.
    profile = parse_capability(["rinse_aid", {"rinse_aid_gear": "high"}])
    assert profile.supports("rinse_aid")
    assert profile.gear_max("rinse_aid") is None
