"""Capability cache lifecycle: what the coordinator resolves, keeps and refreshes.

Resolving capabilities costs cloud requests and decides which entities exist, so the cache
has three hard requirements this covers: a fresh profile is not re-fetched, a stale one is,
and a cloud failure must never silently strip a device of its gated entities.
"""

from dataclasses import replace
from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.holabrain.aiodollin import Device
from custom_components.holabrain.aiodollin.dto.capability import parse_capability
from custom_components.holabrain.aiodollin.exceptions import NetworkError
from custom_components.holabrain.const import DOMAIN
from custom_components.holabrain.coordinator import CAPABILITY_TTL, HolabrainCoordinator

_DEVICE = Device(
    thing_code="thing-1",
    name="Dishwasher",
    device_type="0xE1",
    model="760EY179",
    online=True,
)


class _FakeCapabilities:
    def __init__(self, features=("rinse_aid", "salt"), error=None):
        self.calls = 0
        self.features = list(features)
        self.error = error

    async def async_resolve(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return parse_capability(
            self.features, model=kwargs["model"], device_type=kwargs["device_type"]
        )


def _coordinator(hass, entry, capabilities):
    client = SimpleNamespace(capabilities=capabilities)
    coordinator = HolabrainCoordinator(hass, entry, client)
    coordinator.devices = {_DEVICE.thing_code: _DEVICE}
    return coordinator


async def test_profile_is_resolved_once_and_reused_from_storage(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    first = _FakeCapabilities()
    coordinator = _coordinator(hass, entry, first)
    await coordinator._async_load_capabilities()
    assert coordinator.capability_for("thing-1").supports("rinse_aid")
    assert first.calls == 1

    # A second setup (restart) must come straight from storage — no cloud request.
    second = _FakeCapabilities()
    restarted = _coordinator(hass, entry, second)
    await restarted._async_load_capabilities()
    assert second.calls == 0
    assert restarted.capability_for("thing-1").supports("salt")


async def test_expired_profile_is_re_resolved(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    coordinator = _coordinator(hass, entry, _FakeCapabilities())
    await coordinator._async_load_capabilities()

    # Age the cached profile past the TTL and reload.
    cached = coordinator.capabilities["760EY179"]
    coordinator.capabilities["760EY179"] = replace(
        cached, fetched_at=cached.fetched_at - CAPABILITY_TTL.total_seconds() - 1
    )
    await coordinator._async_save_cache()

    refreshed = _FakeCapabilities()
    reloaded = _coordinator(hass, entry, refreshed)
    await reloaded._async_load_capabilities()
    assert refreshed.calls == 1


async def test_cloud_failure_keeps_the_cached_profile(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    coordinator = _coordinator(hass, entry, _FakeCapabilities())
    await coordinator._async_load_capabilities()

    offline = _coordinator(hass, entry, _FakeCapabilities(error=NetworkError("cloud down")))
    await offline._async_load_capabilities()
    # Entities stay: a refresh that could not run must not read as "supports nothing".
    assert offline.capability_for("thing-1").supports("rinse_aid")


async def test_forced_refresh_reports_a_changed_feature_set(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    coordinator = _coordinator(hass, entry, _FakeCapabilities())
    await coordinator._async_load_capabilities()

    coordinator._client = SimpleNamespace(
        capabilities=_FakeCapabilities(features=("rinse_aid", "salt", "auto_open"))
    )
    assert await coordinator.async_refresh_capabilities(force=True) is True
    assert coordinator.capability_for("thing-1").supports("auto_open")
    # Nothing new the second time round.
    assert await coordinator.async_refresh_capabilities(force=True) is False


async def test_legacy_config_entry_cache_is_migrated_out_of_the_entry(hass):
    legacy = {"760EY179": {"features": ["rinse_aid"], "params": {}}}
    entry = MockConfigEntry(domain=DOMAIN, data={"account": "a@b.c", "capability_cache": legacy})
    entry.add_to_hass(hass)

    capabilities = _FakeCapabilities()
    coordinator = _coordinator(hass, entry, capabilities)
    await coordinator._async_load_capabilities()

    assert "capability_cache" not in entry.data
    # The migrated record predates the current schema, so it is re-resolved once.
    assert capabilities.calls == 1
    assert coordinator.capability_for("thing-1").supports("rinse_aid")


async def test_reported_status_keys_are_absorbed_into_the_profile(hass):
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)

    coordinator = _coordinator(hass, entry, _FakeCapabilities())
    await coordinator._async_load_capabilities()

    from custom_components.holabrain.aiodollin import DeviceState

    state = DeviceState.from_query("thing-1", {"power": "1", "probeTemp": "35"})
    coordinator._absorb_status_fields({"thing-1": state})
    profile = coordinator.capability_for("thing-1")
    assert profile.has_field("probeTemp")
    # Presence gating goes through the same question the platforms ask.
    assert profile.supports("probeTemp")
