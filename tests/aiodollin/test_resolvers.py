"""Capability resolver chain tests.

The whole point of the chain is that *every* appliance family gets an honest answer to
"does it support X?" from whatever source that family actually uses. These tests exercise
one real failure mode per strategy, plus the merge semantics that decide what the Home
Assistant adapter is allowed to create.
"""

import pytest

from custom_components.holabrain.aiodollin.api.capabilities import (
    BitfieldResolver,
    CapabilityApi,
    DictGetResolver,
    PresenceRule,
    ResolutionContext,
    ResolverChain,
    StaticResolver,
    StaticVariant,
    StatusPresenceResolver,
    build_default_chains,
)
from custom_components.holabrain.aiodollin.bitfield import (
    AC_DYNAMIC_ATTR_LAYOUT,
    BitField,
    BitfieldLayout,
    decode_bitfield,
)
from custom_components.holabrain.aiodollin.exceptions import ApiError, NetworkError


def _ctx(**kwargs):
    kwargs.setdefault("device_type", "0xAC")
    kwargs.setdefault("model", "TESTSN80")
    return ResolutionContext(**kwargs)


def _pack(values: dict[str, int]) -> bytes:
    """Pack fields in layout order, LSB first — the inverse of decode_bitfield()."""
    bits: list[int] = []
    for field in AC_DYNAMIC_ATTR_LAYOUT.fields:
        value = values.get(field.name, 0)
        bits.extend((value >> i) & 1 for i in range(field.width))
    for name in AC_DYNAMIC_ATTR_LAYOUT.trailing_u32:
        value = values.get(name, 0)
        bits.extend((value >> i) & 1 for i in range(32))
    data = bytearray((len(bits) + 7) // 8)
    for index, bit in enumerate(bits):
        if bit:
            data[index // 8] |= 1 << (index % 8)
    return bytes(data)


# -- bitfield ------------------------------------------------------------------------------


def test_bitfield_round_trip_preserves_field_order():
    blob = _pack({"isEcoFunc": 1, "isColdandWarm": 5, "tempMinimum": 16, "tempMaximum": 32})
    decoded = decode_bitfield(blob)
    assert decoded["isEcoFunc"] == 1
    assert decoded["is8DegreeHeatFunc"] == 0
    assert decoded["isColdandWarm"] == 5
    assert decoded["tempMinimum"] == 16
    assert decoded["tempMaximum"] == 32


def test_truncated_descriptor_yields_absent_not_zero_fields():
    # A short blob must not fabricate "supported = 0" for the fields it never covered:
    # absent is the only honest answer, and the resolver treats it as unsupported.
    layout = BitfieldLayout(fields=(BitField("a", 4), BitField("b", 8)))
    decoded = decode_bitfield(b"\x0f", layout)
    assert decoded == {"a": 15}


def test_invalid_base64_is_reported_as_api_error():
    with pytest.raises(ApiError):
        decode_bitfield("not base64 @@@")


async def test_bitfield_resolver_gates_optional_controls():
    import base64

    blob = base64.b64encode(
        _pack({"isEcoFunc": 1, "isDecimalTemp": 1, "tempMinimum": 16, "tempMaximum": 31})
    ).decode()
    profile = await BitfieldResolver().async_resolve(
        _ctx(metadata={"dynamicAttr": blob})
    )
    assert profile.supports("eco")
    # Not advertised → the switch must not exist at all.
    assert not profile.supports("self_clean")
    assert not profile.supports("heat_8c")
    assert profile.param("temp_step") == 0.5
    assert profile.temperature_range((17, 30)) == (16.0, 31.0)


async def test_bitfield_resolver_without_descriptor_still_gives_a_temp_range():
    profile = await BitfieldResolver().async_resolve(_ctx(metadata={}))
    assert profile.features == frozenset()
    assert profile.temperature_range((0, 0)) == (17.0, 30.0)


async def test_out_of_range_temperature_block_falls_back_to_defaults():
    import base64

    blob = base64.b64encode(_pack({"tempMinimum": 0, "tempMaximum": 9999})).decode()
    profile = await BitfieldResolver().async_resolve(_ctx(metadata={"dynamicAttr": blob}))
    assert profile.temperature_range((0, 0)) == (17.0, 30.0)


# -- status presence -----------------------------------------------------------------------


async def test_presence_resolver_derives_flags_and_records_fields():
    resolver = StatusPresenceResolver(
        [PresenceRule("food_probe", ("probeTemp",)), PresenceRule("steam", ("waterTankStatus",))]
    )
    profile = await resolver.async_resolve(
        _ctx(device_type="0xB1", status={"probeTemp": "35", "power": "1"})
    )
    assert profile.supports("food_probe")
    assert not profile.supports("steam")
    assert profile.has_field("power")


async def test_presence_resolver_fetches_status_only_when_none_is_known():
    calls: list[str] = []

    async def provider(thing_code):
        calls.append(thing_code)
        return {"probeTemp": "35"}

    resolver = StatusPresenceResolver(
        [PresenceRule("food_probe", ("probeTemp",))], status_provider=provider
    )
    assert (await resolver.async_resolve(_ctx(thing_code="t1"))).supports("food_probe")
    await resolver.async_resolve(_ctx(thing_code="t1", status={"power": "1"}))
    assert calls == ["t1"]


async def test_discovered_fields_never_shrink():
    resolver = StatusPresenceResolver([PresenceRule("food_probe", ("probeTemp",))])
    full = await resolver.async_resolve(_ctx(status={"probeTemp": "35", "power": "1"}))
    # A later truncated response must not remove entities that already exist.
    assert full.with_fields({"power"}).has_field("probeTemp")


# -- static tables -------------------------------------------------------------------------


async def test_static_resolver_applies_model_variant_over_baseline():
    resolver = StaticResolver(
        base=StaticVariant(features=frozenset({"power"}), params={"temp_max": 80}),
        variants={
            "51020ED1": StaticVariant(
                features=frozenset({"disinfect"}), params={"temp_max": 75}
            )
        },
    )
    variant = await resolver.async_resolve(_ctx(device_type="0xE2", model="51020ED1"))
    plain = await resolver.async_resolve(_ctx(device_type="0xE2", model="99999999"))
    assert variant.supports("disinfect") and variant.param("temp_max") == 75
    assert not plain.supports("disinfect") and plain.param("temp_max") == 80


# -- chain semantics -----------------------------------------------------------------------


class _Stub:
    def __init__(self, name, profile=None, error=None, required=False):
        self.name = name
        self.required = required
        self._profile = profile
        self._error = error

    async def async_resolve(self, ctx):
        if self._error is not None:
            raise self._error
        return self._profile


async def test_chain_merges_partials_and_later_params_win():
    from custom_components.holabrain.aiodollin.dto.capability import parse_capability

    chain = ResolverChain(
        [
            _Stub("static", parse_capability(["power"], extra_params={"temp_max": 80})),
            _Stub("dict", parse_capability(["eco"], extra_params={"temp_max": 75})),
        ]
    )
    profile = await chain.async_resolve(_ctx())
    assert profile.supports("power") and profile.supports("eco")
    assert profile.param("temp_max") == 75
    assert profile.sources == ("static", "dict")


async def test_chain_keeps_going_when_an_optional_source_fails():
    from custom_components.holabrain.aiodollin.dto.capability import parse_capability

    chain = ResolverChain(
        [
            _Stub("dict", error=NetworkError("cloud down"), required=False),
            _Stub("presence", parse_capability(["eco"])),
        ]
    )
    assert (await chain.async_resolve(_ctx())).supports("eco")


async def test_chain_reraises_when_a_required_source_fails_alone():
    # The caller must be able to tell "nothing resolved" apart from "resolved as empty", so
    # it can keep serving the cached profile instead of deleting every gated entity.
    chain = ResolverChain([_Stub("dict", error=NetworkError("cloud down"), required=True)])
    with pytest.raises(NetworkError):
        await chain.async_resolve(_ctx())


# -- cloud dictionary payload shapes -------------------------------------------------------


class _FakeAuth:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def oem(self, path, body=None):
        self.calls.append((path, body))
        return {"data": self.payload}


async def test_dict_resolver_reads_the_object_shaped_payload():
    auth = _FakeAuth(
        {
            "module": ["rinse_aid", {"rinse_aid_gear": "5"}],
            "sn8Config": {"moduleParams": {"mode": ["auto", "eco"]}, "openType": 2},
        }
    )
    profile = await DictGetResolver(auth).async_resolve(_ctx(device_type="0xE1", model="760EY179"))
    assert profile.gear_max("rinse_aid") == 5
    assert profile.mode_subset() == ["auto", "eco"]
    assert profile.param("open_type") == "2"


async def test_dict_resolver_reads_the_json_string_payload():
    auth = _FakeAuth('["salt", {"salt_gear": "6"}]')
    profile = await DictGetResolver(auth).async_resolve(_ctx(device_type="0xE1", model="760EY179"))
    assert profile.gear_max("salt") == 6


# -- per-family wiring ---------------------------------------------------------------------


async def test_every_supported_family_has_a_chain():
    chains = build_default_chains(_FakeAuth([]))
    assert set(chains) == {"0xE1", "0xAC", "0x13", "0xB1", "0xDB", "0xE2"}


async def test_unknown_family_falls_back_to_presence_only():
    api = CapabilityApi(_FakeAuth([]))
    profile = await api.async_resolve(
        device_type="0xFF", model="whatever", status={"power": "1"}
    )
    assert profile.has_field("power")
    assert profile.features == frozenset()


async def test_dishwasher_chain_still_uses_the_cloud_dictionary():
    auth = _FakeAuth(["rinse_aid", "salt", {"salt_gear": "6"}])
    api = CapabilityApi(auth)
    profile = await api.async_resolve(
        device_type="0xE1", model="760EY179", status={"doorstatus": "1"}
    )
    assert profile.supports("rinse_aid")
    assert not profile.supports("auto_open")
    assert profile.has_field("doorstatus")
    assert auth.calls  # the dictionary really was consulted


async def test_water_heater_variant_narrows_the_temperature_range():
    api = CapabilityApi(_FakeAuth([]))
    plugin_model = await api.async_resolve(device_type="0xE2", model="51020ED1")
    unknown_model = await api.async_resolve(device_type="0xE2", model="00000000")
    assert plugin_model.temperature_range((0, 0)) == (35.0, 75.0)
    assert plugin_model.supports("disinfect")
    assert unknown_model.temperature_range((0, 0)) == (30.0, 80.0)
    assert not unknown_model.supports("disinfect")


async def test_washer_auto_dose_needs_the_dosing_fields():
    api = CapabilityApi(_FakeAuth([]))
    with_dose = await api.async_resolve(
        device_type="0xDB", model="38127413", status={"complianceDose": "30"}
    )
    without = await api.async_resolve(
        device_type="0xDB", model="38127413", status={"cycle": "1"}
    )
    assert with_dose.supports("auto_dose")
    assert not without.supports("auto_dose")
    assert with_dose.param("capacity_kg") == 12
