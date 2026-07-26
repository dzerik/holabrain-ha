"""Account scanning: explicit, never automatic.

Listing the account needs the account session, and the cloud allows exactly one — claiming
it signs the vendor's mobile app out. So scanning is something the user asks for (options
flow, panel button, or the ``holabrain.scan_devices`` service), and normal operation must
never do it on its own. These tests assert both halves of that contract.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.holabrain import async_remove_config_entry_device
from custom_components.holabrain.aiodollin import DiscoveredAppliance
from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL, DOMAIN
from tests.conftest import (
    DISHWASHER_CODE,
    DISHWASHER_STATE,
    LAMP_CODE,
    FakeCloud,
    MqttSpy,
)


async def _scan(hass: HomeAssistant) -> None:
    await hass.services.async_call(DOMAIN, "scan_devices", {}, blocking=True)
    await hass.async_block_till_done()


def _entity_count(hass: HomeAssistant, unique_id_fragment: str) -> int:
    registry = er.async_get(hass)
    return sum(
        1 for entry in registry.entities.values() if unique_id_fragment in entry.unique_id
    )


# --- the contract: monitoring never touches the account inventory ------------------------


async def test_normal_operation_never_lists_the_account(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """Polling and push must not trigger an inventory read.

    Every inventory read claims the account session and logs the mobile app out. If routine
    operation did that on a timer, the app would be signed out repeatedly with no user
    action at all — the exact problem this design avoids.
    """
    assert await setup_integration()
    before = cloud.calls(FakeCloud.DEVICES)

    for index in range(1, 6):
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=(DEFAULT_SCAN_INTERVAL + 5) * index)
        )
        await hass.async_block_till_done()
    mqtt_spy.last.deliver(f"eu/eu_{DISHWASHER_CODE}/dev", {"status": {"washingState": "3"}})
    await hass.async_block_till_done()

    assert cloud.calls(FakeCloud.DEVICES) == before


# --- the explicit scan --------------------------------------------------------------------


async def test_scanning_picks_up_an_appliance_paired_later(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The point of the scan: a device paired in the vendor app shows up on request."""
    assert await setup_integration()
    assert hass.states.get("light.ceiling_lamp") is None

    cloud.add_lamp()
    await _scan(hass)

    assert hass.states.get("light.ceiling_lamp") is not None


async def test_a_new_appliance_is_gated_by_its_own_capabilities(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Capabilities must be resolved before the new device's entities are built.

    Otherwise every optional feature would be gated off on the first pass and appear only
    after another scan — entities flickering into existence is worse than a short delay.
    """
    assert await setup_integration()

    stripped_state = {
        key: value
        for key, value in DISHWASHER_STATE.items()
        if key
        not in (
            "salt",
            "brightenAgent",
            "saltTimes",
            "brightenAgentTimes",
            "distributorGear",
            "softWaterGear",
            "autoDoorOpen",
        )
    }
    cloud.add_dishwasher(
        thing_code="999888777666555",
        name="Spare Dishwasher",
        model="STRIPPED",
        capability=[],
        state=stripped_state,
    )
    await _scan(hass)

    assert _entity_count(hass, "999888777666555") > 0
    registry = er.async_get(hass)
    gated = [
        entry.unique_id
        for entry in registry.entities.values()
        if entry.unique_id.startswith("999888777666555")
        and entry.unique_id.endswith(("_salt", "_brightenAgent", "_distributorGear"))
    ]
    assert gated == []


async def test_scanning_subscribes_the_new_device_to_push(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """Without a subscription the new appliance would depend on polling, and polling is
    exactly what competes for the account session."""
    assert await setup_integration()
    broker = mqtt_spy.last
    before = set(broker.subscriptions)

    cloud.add_lamp()
    await _scan(hass)

    assert any(LAMP_CODE in topic for topic in set(broker.subscriptions) - before)


async def test_scanning_drops_an_appliance_removed_from_the_account(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Unbinding in the vendor app must stop the polling for that appliance."""
    assert await setup_integration()
    cloud.add_lamp()
    await _scan(hass)
    assert hass.states.get("light.ceiling_lamp") is not None

    cloud.devices = [d for d in cloud.devices if d["thingCode"] != LAMP_CODE]
    await _scan(hass)

    cloud.requests.clear()
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 5)
    )
    await hass.async_block_till_done()

    polled = {
        path.rsplit("/", 1)[-1]
        for kind, path, _ in cloud.requests
        if kind == FakeCloud.QUERY
    }
    assert LAMP_CODE not in polled


async def test_repeated_scans_do_not_churn_entities(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Scanning an unchanged account must be a no-op.

    Recreating entities would break their history and any automation bound to them.
    """
    assert await setup_integration()
    before = _entity_count(hass, DISHWASHER_CODE)

    await _scan(hass)
    await _scan(hass)

    assert _entity_count(hass, DISHWASHER_CODE) == before


async def test_each_scan_costs_exactly_one_inventory_read(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """One user action, one account read — the cost has to stay predictable."""
    assert await setup_integration()
    before = cloud.calls(FakeCloud.DEVICES)

    await _scan(hass)
    await _scan(hass)

    assert cloud.calls(FakeCloud.DEVICES) - before == 2


async def test_a_failed_scan_is_reported_and_keeps_the_existing_devices(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """A failed scan must surface to the user, not silently wipe the appliances.

    Treating an unreachable cloud as "the account is now empty" would delete every entity.
    """
    assert await setup_integration()
    before = _entity_count(hass, DISHWASHER_CODE)
    cloud.fail_next(FakeCloud.DEVICES, TimeoutError("cloud unreachable"))

    with pytest.raises(Exception):  # noqa: B017 - the transport error type is not the point
        await _scan(hass)

    assert _entity_count(hass, DISHWASHER_CODE) == before
    assert hass.states.get("sensor.dishwasher_wash_stage") is not None


async def test_scanning_without_a_loaded_entry_is_rejected(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """Calling the service while the entry is unloaded must fail loudly, not do nothing."""
    assert await setup_integration()
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    with pytest.raises(ServiceValidationError):
        await _scan(hass)


# --- the same scan, from the integration options -----------------------------------------


async def test_options_flow_scan_confirms_before_touching_the_account(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """Opening the scan step must not scan yet.

    The step exists to show the warning that the mobile app will be signed out; scanning
    before the user confirms would make that warning pointless.
    """
    assert await setup_integration()
    before = cloud.calls(FakeCloud.DEVICES)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "scan"}
    )

    assert result["type"] == "form"
    assert result["step_id"] == "scan"
    assert cloud.calls(FakeCloud.DEVICES) == before


async def test_options_flow_scan_reports_what_it_found(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """Confirming performs the scan and reports the outcome back to the user."""
    assert await setup_integration()
    cloud.add_lamp()

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "scan"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={})
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "scan_done"
    assert result["description_placeholders"] == {"added": "1", "removed": "0"}
    assert hass.states.get("light.ceiling_lamp") is not None


async def test_options_flow_scan_reports_a_failure_instead_of_pretending(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """A cloud failure must end the flow with an error, not a cheerful '0 added'."""
    assert await setup_integration()
    cloud.fail_next(FakeCloud.DEVICES, TimeoutError("cloud unreachable"))

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "scan"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={})

    assert result["type"] == "abort"
    assert result["reason"] == "scan_failed"


async def test_options_flow_scan_asks_for_reauthentication_when_rejected(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """If the account itself refuses, saying so is more useful than a generic failure."""
    assert await setup_integration()
    cloud.fail_next(FakeCloud.DEVICES, {"code": 14005, "msg": "unusual activity"}, times=3)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "scan"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], user_input={})

    assert result["type"] == "abort"
    assert result["reason"] == "scan_auth_failed"


# --- cleanup: an appliance the account no longer has must not linger ----------------------


def _device_entry(hass: HomeAssistant, thing_code: str):
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, thing_code)})


async def test_scanning_deletes_an_appliance_that_left_the_account(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """An unbound appliance must be removed, not left as a permanently unavailable device.

    Keeping it leaves a dead device and a dozen dead entities that still appear in entity
    pickers, dashboards and automations — and they can never recover, because the appliance
    is gone from the account.
    """
    assert await setup_integration()
    cloud.add_lamp()
    await _scan(hass)
    assert _device_entry(hass, LAMP_CODE) is not None
    assert _entity_count(hass, LAMP_CODE) > 0

    cloud.devices = [d for d in cloud.devices if d["thingCode"] != LAMP_CODE]
    await _scan(hass)

    assert _device_entry(hass, LAMP_CODE) is None
    assert _entity_count(hass, LAMP_CODE) == 0


async def test_deleting_one_appliance_leaves_the_others_untouched(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Cleanup must be surgical: removing one appliance cannot take the account with it."""
    assert await setup_integration()
    cloud.add_lamp()
    await _scan(hass)
    dishwasher_entities = _entity_count(hass, DISHWASHER_CODE)

    cloud.devices = [d for d in cloud.devices if d["thingCode"] != LAMP_CODE]
    await _scan(hass)

    assert _entity_count(hass, DISHWASHER_CODE) == dishwasher_entities
    assert hass.states.get("sensor.dishwasher_wash_stage") is not None


async def test_a_failed_scan_never_deletes_anything(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """An unreachable cloud must not be read as 'the account is empty'.

    This is the dangerous direction of the feature: deleting on a failure would wipe every
    device the user has, irreversibly.
    """
    assert await setup_integration()
    cloud.fail_next(FakeCloud.DEVICES, TimeoutError("cloud unreachable"))

    with pytest.raises(Exception):  # noqa: B017 - the transport error type is not the point
        await _scan(hass)

    assert _device_entry(hass, DISHWASHER_CODE) is not None
    assert _entity_count(hass, DISHWASHER_CODE) > 0


async def test_a_live_appliance_cannot_be_deleted_from_the_ui(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """The delete button must be refused while the appliance is still on the account.

    Otherwise a stray click removes a working device, and the next scan brings it back
    without its customisations — the user loses names, areas and entity ids for nothing.
    """
    assert await setup_integration()
    device = _device_entry(hass, DISHWASHER_CODE)
    assert device is not None

    allowed = await async_remove_config_entry_device(hass, config_entry, device)

    assert allowed is False


async def test_a_stale_appliance_can_be_deleted_from_the_ui(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, config_entry
) -> None:
    """A device left over from an older setup must be removable by hand."""
    assert await setup_integration()
    device = _device_entry(hass, DISHWASHER_CODE)
    coordinator = config_entry.runtime_data.coordinator
    coordinator.devices = {}  # the account no longer lists it

    allowed = await async_remove_config_entry_device(hass, config_entry, device)

    assert allowed is True


async def test_an_appliance_unbound_while_offline_is_cleaned_up_on_startup(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """A device left in the registry from a previous run must not survive a restart.

    If the appliance is unbound while Home Assistant is down, the coordinator never sees it
    disappear — it simply never appears. Without reconciling the registry against the first
    inventory read, that device stays forever as an unavailable leftover that no scan can
    ever remove, because nothing knows it used to exist.
    """
    assert await setup_integration()
    assert _device_entry(hass, DISHWASHER_CODE) is not None

    # The appliance is unbound in the vendor app while Home Assistant is not running.
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    cloud.devices = [d for d in cloud.devices if d["thingCode"] != DISHWASHER_CODE]

    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert _device_entry(hass, DISHWASHER_CODE) is None
    assert _entity_count(hass, DISHWASHER_CODE) == 0


async def test_startup_keeps_devices_the_account_still_lists(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """Reconciling must not become a device shredder on every restart."""
    assert await setup_integration()
    before = _entity_count(hass, DISHWASHER_CODE)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert _device_entry(hass, DISHWASHER_CODE) is not None
    assert _entity_count(hass, DISHWASHER_CODE) == before


# --- claiming an appliance found on the local network -------------------------------------


def _found(device_id: str, serial: str, model: str = "LAMP0001", dtype: str = "0x13"):
    return DiscoveredAppliance(
        device_id=device_id,
        serial=serial,
        model=model,
        device_type=dtype,
        host="192.0.2.63",
        port=6444,
    )


def _patch_search(*appliances):
    return patch(
        "custom_components.holabrain.config_flow.async_discover",
        return_value=list(appliances),
    )


async def _open_add(hass: HomeAssistant, config_entry):
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "add_appliance"}
    )


async def test_adding_an_appliance_found_on_the_network(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """The happy path: it is found on the LAN, claimed, and appears as entities."""
    assert await setup_integration()
    cloud.claimable["SN-LAMP"] = ("990011", "aabbccddeeff00112233445566778899")

    with _patch_search(_found("990011", "SN-LAMP")):
        result = await _open_add(hass, config_entry)
        assert result["step_id"] == "claim"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "appliance": "990011",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "wifi_password": "secret",
            },
        )
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "appliance_added"
    assert cloud.bound == [("990011", "0x13")]


async def test_the_serial_is_read_from_the_network_not_typed(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """The form must not ask for a serial: the appliance already told us its own.

    Reading a 32-character code off a label and retyping it is the single most error-prone
    step of the whole flow, and it is unnecessary.
    """
    assert await setup_integration()
    cloud.claimable["SN-LAMP"] = ("990011", "00" * 16)

    with _patch_search(_found("990011", "SN-LAMP")):
        result = await _open_add(hass, config_entry)

    assert "serial" not in result["data_schema"].schema
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_appliances_already_on_the_account_are_not_offered(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """Offering an appliance that is already bound would only produce a confusing error."""
    assert await setup_integration()

    with _patch_search(_found(DISHWASHER_CODE, "SN-DISH", "760EY179", "0xE1")):
        result = await _open_add(hass, config_entry)

    assert result["type"] == "abort"
    assert result["reason"] == "nothing_to_add"


async def test_an_empty_network_is_reported_as_such(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """"Nothing answered" needs different advice from "everything is already added"."""
    assert await setup_integration()

    with _patch_search():
        result = await _open_add(hass, config_entry)

    assert result["type"] == "abort"
    assert result["reason"] == "no_appliances_found"


async def test_an_appliance_not_in_setup_mode_is_reported_distinctly(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """"Hold the pairing button" is different advice from "the cloud does not know it"."""
    assert await setup_integration()
    cloud.known_serials.add("SN-IDLE")  # known to the cloud, but not offering itself

    with _patch_search(_found("990011", "SN-IDLE")):
        result = await _open_add(hass, config_entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "appliance": "990011",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "wifi_password": "secret",
            },
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "not_claimable"}


async def test_a_serial_unknown_to_the_cloud_is_named(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """An appliance on the LAN that the cloud has never seen is its own situation."""
    assert await setup_integration()

    with _patch_search(_found("990011", "SN-STRANGER")):
        result = await _open_add(hass, config_entry)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "appliance": "990011",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "wifi_password": "secret",
            },
        )

    assert result["errors"] == {"base": "serial_unknown"}


async def test_the_wifi_details_are_remembered_for_next_time(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """Retyping the Wi-Fi password for every appliance is pointless friction."""
    assert await setup_integration()
    cloud.claimable["SN-LAMP"] = ("990011", "00" * 16)

    with _patch_search(_found("990011", "SN-LAMP")):
        result = await _open_add(hass, config_entry)
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "appliance": "990011",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "wifi_password": "secret",
            },
        )
    await hass.async_block_till_done()

    assert config_entry.data["bssid"] == "aa:bb:cc:dd:ee:ff"
    assert config_entry.data["wifi_password"] == "secret"

    # The next run offers them as defaults instead of asking again.
    cloud.claimable["SN-TWO"] = ("990022", "00" * 16)
    with _patch_search(_found("990022", "SN-TWO")):
        result = await _open_add(hass, config_entry)
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if key.default
    }
    assert defaults["bssid"] == "aa:bb:cc:dd:ee:ff"
    hass.config_entries.options.async_abort(result["flow_id"])


async def test_the_serial_is_encrypted_before_it_is_sent(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """The serial identifies the appliance; the cloud only accepts it session-encrypted."""
    assert await setup_integration()
    cloud.claimable["SN-LAMP"] = ("990011", "00" * 16)

    with _patch_search(_found("990011", "SN-LAMP")):
        result = await _open_add(hass, config_entry)
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "appliance": "990011",
                "bssid": "aa:bb:cc:dd:ee:ff",
                "wifi_password": "secret",
            },
        )

    sent = [body for kind, _, body in cloud.requests if kind == FakeCloud.VERIFICATION]
    assert sent, "verification was never called"
    assert "SN-LAMP" not in sent[-1]["sn"]
