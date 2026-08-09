"""Config-entry lifecycle: setup, failure classification, unload, reload, second account.

Everything here is about what survives an ugly restart: a cloud that is down at boot, a
token that expired while Home Assistant was off, an entry that is reloaded twice in a row,
and a second account that must keep working when the first one is removed.
"""

from __future__ import annotations

import httpx
import pytest
from homeassistant.config_entries import ConfigEntryState, OperationNotAllowed
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.holabrain.const import CONF_ACCOUNT, CONF_REGION, DOMAIN
from tests.conftest import DISHWASHER_CODE, DISHWASHER_MODEL, FakeCloud, MqttSpy

# A model that reports none of the optional status keys, so nothing is gated in by presence.
BARE_STATE = {
    "power": "1",
    "runState": "2",
    "washingState": "0",
    "modeEU": "4",
    "faultCode": "0",
    "doorstatus": "1",
    "realTemp": "20",
    "remainTimeH": "0",
    "remainTimeL": "0",
    "online": 1,
}


async def test_setup_loads_every_platform_and_registers_the_device(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry
) -> None:
    """A successful setup must produce one device with entities on several platforms.

    This is the smoke wire between the registry, the coordinator and the platforms; if any
    of the three stops agreeing on device ids, entities land under the wrong device or none
    at all.
    """
    assert await setup_integration()
    assert config_entry.state is ConfigEntryState.LOADED

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DISHWASHER_CODE)})
    assert device is not None
    assert device.manufacturer == "HolaBrain"
    assert device.model == "760EY179"
    assert device.sw_version == "059006092306"

    entities = er.async_entries_for_device(
        er.async_get(hass), device.id, include_disabled_entities=True
    )
    platforms = {entry.domain for entry in entities}
    assert {"sensor", "binary_sensor", "switch", "number"} <= platforms


async def test_cloud_unreachable_at_boot_retries_instead_of_failing_permanently(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    """A cloud outage during startup must leave the entry retrying, not broken.

    Home Assistant frequently starts before the network is usable. Turning that into a hard
    setup error would require the user to reload the integration by hand after every reboot.
    """
    cloud.fail_next(FakeCloud.DEVICES, httpx.ConnectError("network is unreachable"), times=5)

    assert not await setup_integration()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.states.async_entity_ids(DOMAIN) == []


async def test_failed_setup_closes_the_http_client_it_opened(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """A setup that raises must not leak its HTTP client.

    Setup is retried on a timer, so one leaked client per attempt turns a temporary outage
    into an unbounded resource leak.
    """
    cloud.fail_next(FakeCloud.DEVICES, httpx.ConnectError("down"), times=5)

    await setup_integration()

    assert cloud.owned_clients
    assert all(client.is_closed for client in cloud.owned_clients)


async def test_rejected_token_at_boot_is_an_auth_failure_not_a_retry(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    """Credentials the cloud refuses must stop the entry instead of hammering the API.

    Retrying a login the cloud has rejected every 30 seconds is how an account gets locked;
    the entry has to go to an auth-error state so the user is asked to sign in again.

    Business code 3114016 (`CredentialsRejectedError`), not 14005: since this branch split
    the auth codes, 14005 means an expired token, which *is* safe to retry — see
    `AuthManager`'s expiry budget and `docs/superpowers/specs/2026-08-09-token-refresh-
    design.md`'s "Known risk". A rejected account/password is what must not be retried.
    """
    # The account list request answers with an auth code, and the re-login is refused too.
    cloud.fail_next(FakeCloud.DEVICES, {"code": 3114016, "msg": "wrong credentials"})
    cloud.fail_next(FakeCloud.LOGIN, {"code": 3114016, "msg": "wrong credentials"}, times=3)

    assert not await setup_integration()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.states.async_entity_ids(DOMAIN) == []


async def test_unload_releases_the_push_connection_and_the_http_client(
    hass: HomeAssistant,
    setup_integration,
    config_entry: MockConfigEntry,
    cloud: FakeCloud,
    mqtt_spy: MqttSpy,
) -> None:
    """Unloading must tear down everything the setup started.

    A push connection that outlives its config entry keeps pushing into a dead coordinator
    and keeps a TLS socket open forever.
    """
    assert await setup_integration()
    broker = mqtt_spy.last
    assert broker.connected

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    assert broker.disconnect_calls == 1
    assert not broker.connected
    assert all(client.is_closed for client in cloud.owned_clients)
    assert hass.states.async_entity_ids(DOMAIN) == []


async def test_setting_up_an_already_loaded_entry_is_refused(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry, mqtt_spy: MqttSpy
) -> None:
    """A double setup must not build a second coordinator or a second push connection.

    Two coordinators on one account double the polling rate and fight over optimistic state;
    the guard belongs to Home Assistant, and this test makes sure nothing here defeats it.
    """
    assert await setup_integration()
    assert len(mqtt_spy.instances) == 1

    with pytest.raises(OperationNotAllowed):
        await hass.config_entries.async_setup(config_entry.entry_id)

    assert len(mqtt_spy.instances) == 1


async def test_unload_setup_unload_never_double_tears_down(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry, mqtt_spy: MqttSpy
) -> None:
    """Manual disable/enable cycles must be idempotent.

    Unloading an already-unloaded entry must not run the teardown again (the runtime data is
    gone by then, so a second pass would raise), and setting up afterwards must produce a
    fresh push connection rather than resurrecting the closed one.
    """
    assert await setup_integration()
    first = mqtt_spy.last

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    # Repeating the unload is a no-op, not a second teardown.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert first.disconnect_calls == 1

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert mqtt_spy.last is not first
    assert mqtt_spy.last.connected
    assert first.disconnect_calls == 1


async def test_reload_replaces_the_push_connection_without_leaking_the_old_one(
    hass: HomeAssistant,
    setup_integration,
    config_entry: MockConfigEntry,
    mqtt_spy: MqttSpy,
    cloud: FakeCloud,
) -> None:
    """Reloading twice must leave exactly one live push connection and no duplicate entities.

    Reload is the standard recovery action and the standard way to leak: each cycle that
    forgets to disconnect leaves a subscriber behind, and each cycle that changes unique ids
    orphans the previous entities.
    """
    assert await setup_integration()
    before = set(hass.states.async_entity_ids())

    for _ in range(2):
        assert await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.LOADED
    assert set(hass.states.async_entity_ids()) == before
    assert len(mqtt_spy.instances) == 3
    assert [broker.connected for broker in mqtt_spy.instances] == [False, False, True]
    assert all(broker.disconnect_calls == 1 for broker in mqtt_spy.instances[:-1])


async def test_second_account_keeps_working_when_the_first_is_removed(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    """Removing one account must not disable the integration for the other one.

    Integration-wide registrations (services) are torn down on unload; doing that while a
    second entry is still loaded silently breaks that account's automations.
    """
    assert await setup_integration()

    second = MockConfigEntry(
        domain=DOMAIN,
        title="second@example.com",
        unique_id="second@example.com",
        data={
            CONF_ACCOUNT: cloud.account,
            "password": cloud.password,
            CONF_REGION: "eu",
            "country": "RU",
        },
    )
    second.add_to_hass(hass)
    assert await hass.config_entries.async_setup(second.entry_id)
    await hass.async_block_till_done()

    services_while_both_loaded = set(hass.services.async_services_for_domain(DOMAIN))
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert second.state is ConfigEntryState.LOADED
    assert set(hass.services.async_services_for_domain(DOMAIN)) == services_while_both_loaded


async def test_two_accounts_do_not_share_entity_unique_ids(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Two appliances of the same model must produce distinct entities.

    Unique ids are derived from the device id; if a key ever leaks into them alone, the
    second appliance silently overwrites the first one's entities.
    """
    cloud.add_dishwasher("999900001111222", "Second Dishwasher")

    assert await setup_integration()

    registry = er.async_get(hass)
    unique_ids = [
        entry.unique_id for entry in registry.entities.values() if entry.platform == DOMAIN
    ]
    assert len(unique_ids) == len(set(unique_ids))
    assert any(uid.startswith("999900001111222_") for uid in unique_ids)
    assert any(uid.startswith(f"{DISHWASHER_CODE}_") for uid in unique_ids)


# --- services -----------------------------------------------------------------------------


async def test_refresh_capabilities_service_rebuilds_the_entity_set(
    hass: HomeAssistant, cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """The manual refresh must actually change what exists, not just re-fetch quietly.

    Its only reason to exist is the case where a profile was resolved while the cloud was
    degraded: re-resolving without rebuilding the entities leaves the user exactly where
    they were, with no indication that anything happened.
    """
    cloud.devices.clear()
    cloud.states.clear()
    cloud.add_dishwasher(state=BARE_STATE, capability=[])
    assert await setup_integration()
    assert entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear") is None

    cloud.capabilities[DISHWASHER_MODEL] = ["rinse_aid", {"rinse_aid_gear": "4"}]
    await hass.services.async_call(DOMAIN, "refresh_capabilities", {}, blocking=True)
    await hass.async_block_till_done()

    entity_id = entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear")
    assert entity_id is not None
    assert hass.states.get(entity_id).attributes["max"] == 4


async def test_refresh_capabilities_rejects_an_unknown_device(
    hass: HomeAssistant, setup_integration
) -> None:
    """A service call aimed at a device that does not exist must fail loudly.

    Silently refreshing everything instead would make a typo in an automation look like it
    worked, and would fan out cloud requests the user never asked for.
    """
    assert await setup_integration()

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "refresh_capabilities", {"device_id": "does-not-exist"}, blocking=True
        )


async def test_actions_outlive_the_last_entry_and_fail_loudly(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry
) -> None:
    """Actions stay registered while no account is loaded, and refuse to do anything.

    Removing them would break every automation that references one at validation time,
    with an error ("action not found") that says nothing about the actual cause. Keeping
    them means the automation still resolves and the call explains itself — but a call that
    reached a torn-down coordinator would raise an unhandled error, so it must be refused
    before it gets there.
    """
    assert await setup_integration()
    assert hass.services.has_service(DOMAIN, "refresh_capabilities")

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.services.has_service(DOMAIN, "refresh_capabilities")
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "refresh_capabilities", {}, blocking=True)
