"""Coordinator: the poll/push data pump under conditions the happy path never sees.

Every test here is a situation the cloud actually produces: a frame that arrives before the
first snapshot, an appliance that drops off mid-cycle, a status body that comes back empty,
a token that expires between two polls, and a push channel that cannot be established at
all. The requirement in each case is the same — degrade, never lose data and never crash.
"""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.holabrain.aiodollin.exceptions import ApiError
from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL
from tests.conftest import DISHWASHER_CODE, FakeCloud, MqttSpy

DEV_TOPIC = f"eu/eu_{DISHWASHER_CODE}/dev"


async def _advance_one_poll(hass: HomeAssistant) -> None:
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 5)
    )
    await hass.async_block_till_done()


# --- push arriving at the wrong moment ---------------------------------------------------


async def test_push_frame_before_the_first_poll_is_dropped(
    hass: HomeAssistant, make_coordinator, mqtt_spy: MqttSpy
) -> None:
    """A frame delivered between subscribing and the first snapshot must not crash or stick.

    The push subscription is established during setup, so the broker can deliver a retained
    frame before any snapshot exists. Folding it into "no state" would either raise or
    invent a device state built from a partial delta.
    """
    coordinator = make_coordinator()
    await coordinator._async_setup()
    assert coordinator.data is None

    await hass.async_add_executor_job(
        mqtt_spy.last.deliver, DEV_TOPIC, {"status": {"runState": "1"}}
    )
    await hass.async_block_till_done()

    assert coordinator.data is None  # dropped, not synthesised

    # And the first real poll still populates normally afterwards.
    await coordinator.async_refresh()
    assert coordinator.data[DISHWASHER_CODE].get("runState") == "1"


async def test_push_for_an_unknown_device_is_ignored(
    hass: HomeAssistant, setup_integration, push, cloud: FakeCloud
) -> None:
    """A frame for a device this entry does not own must not create phantom state.

    Accounts get devices added from the companion app between polls; the broker may deliver
    for them before the inventory is refreshed.
    """
    assert await setup_integration()
    coordinator = _coordinator(hass)
    before = dict(coordinator.data)

    await push("eu/eu_000000000000000/dev", {"status": {"power": "1"}})

    assert coordinator.data == before


@pytest.mark.parametrize("topic", ["", "dev", "eu", "eu/eu_/dev", "eu//dev"])
async def test_malformed_push_topic_is_ignored(
    hass: HomeAssistant, setup_integration, push, topic: str
) -> None:
    """A topic that does not carry a device id must be dropped, not parsed into one.

    Brokers deliver heartbeat and last-will traffic on neighbouring topics; a parser that
    returns an empty or partial id would route those frames onto a real device.
    """
    assert await setup_integration()
    coordinator = _coordinator(hass)
    before = dict(coordinator.data)

    await push(topic, {"status": {"power": "0"}})

    assert coordinator.data == before


async def test_push_updates_reach_entities_without_waiting_for_a_poll(
    hass: HomeAssistant, setup_integration, push, entity_id_of
) -> None:
    """The whole point of the push channel: state changes before the next poll interval."""
    assert await setup_integration()
    entity_id = entity_id_of("switch", f"{DISHWASHER_CODE}_runState")
    assert hass.states.get(entity_id).state == "on"

    await push(DEV_TOPIC, {"status": {"runState": "2"}})

    assert hass.states.get(entity_id).state == "off"


# --- the appliance drops off -------------------------------------------------------------


async def test_device_going_offline_mid_cycle_makes_its_entities_unavailable(
    hass: HomeAssistant, setup_integration, push, entity_id_of
) -> None:
    """An offline appliance must report unavailable, not its last known values.

    Keeping the last value visible is worse than useless here: automations act on a "door
    closed" that has not been true since the appliance lost power.
    """
    assert await setup_integration()
    door = entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_doorstatus")
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    assert hass.states.get(door).state != "unavailable"

    await push(DEV_TOPIC, {"onlineChange": {"online": 0}})

    assert hass.states.get(door).state == "unavailable"
    assert hass.states.get(stage).state == "unavailable"


async def test_device_coming_back_online_restores_its_entities(
    hass: HomeAssistant, setup_integration, push, entity_id_of
) -> None:
    """Recovery must be automatic — no reload, no restart."""
    assert await setup_integration()
    door = entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_doorstatus")

    await push(DEV_TOPIC, {"onlineChange": {"online": 0}})
    assert hass.states.get(door).state == "unavailable"

    await push(DEV_TOPIC, {"onlineChange": {"online": 1}})
    assert hass.states.get(door).state != "unavailable"


async def test_offline_flag_in_a_polled_snapshot_is_honoured(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Offline can also arrive through the poll path, and must be treated identically.

    The two paths build ``DeviceState`` differently (full snapshot vs delta); a fix applied
    to only one of them leaves half the users with stale-looking entities.
    """
    assert await setup_integration()
    door = entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_doorstatus")

    cloud.set_attr(DISHWASHER_CODE, online=0)
    await _advance_one_poll(hass)

    assert hass.states.get(door).state == "unavailable"


# --- broken and partial cloud responses --------------------------------------------------


async def test_a_failed_poll_keeps_the_last_known_state(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """One bad snapshot must not blank the device.

    The cloud regularly answers a single query with an error while everything else works;
    dropping the previous state would make every entity flicker to unknown for a cycle.
    """
    assert await setup_integration()
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    before = hass.states.get(stage).state
    assert before == "main_wash"

    cloud.fail_next(FakeCloud.QUERY, {"code": 3120, "msg": "temporarily unavailable"})
    await _advance_one_poll(hass)

    assert hass.states.get(stage).state == before


async def test_a_status_body_with_no_payload_does_not_wipe_the_device(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """``data: null`` is a real cloud answer and must be treated as a failed poll.

    Parsing it as "an appliance reporting nothing" would clear every attribute and leave a
    device that is online but has no state at all.
    """
    assert await setup_integration()
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")

    cloud.fail_next(FakeCloud.QUERY, {"code": 0, "data": None})
    await _advance_one_poll(hass)

    assert hass.states.get(stage).state == "main_wash"


async def test_a_truncated_snapshot_only_blanks_the_keys_it_omits(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A snapshot missing half its keys must yield unknown, never a wrong value.

    Appliances truncate their status while booting or mid-programme change; a sensor that
    keeps rendering the previous code would report a wash stage the machine left minutes ago.
    """
    assert await setup_integration()
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    temperature = entity_id_of("sensor", f"{DISHWASHER_CODE}_realTemp")

    cloud.states[DISHWASHER_CODE] = {"online": 1, "realTemp": "60"}
    await _advance_one_poll(hass)

    assert hass.states.get(temperature).state == "60"
    assert hass.states.get(stage).state == "unknown"


async def test_one_device_failing_does_not_hold_back_the_others(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A device the cloud cannot answer for must not freeze the rest of the account."""
    second = "999900001111222"
    cloud.add_dishwasher(second, "Second Dishwasher")
    assert await setup_integration()

    # The first device in iteration order fails; the second still has to be polled.
    cloud.fail_next(FakeCloud.QUERY, httpx.ConnectError("reset by peer"))
    cloud.set_attr(second, washingState="4")
    await _advance_one_poll(hass)

    assert hass.states.get(entity_id_of("sensor", f"{second}_washingState")).state == "drying"


async def test_total_cloud_outage_at_boot_leaves_the_entry_retrying(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry, cloud: FakeCloud
) -> None:
    """If no device can be polled at all on the first refresh, setup must not "succeed".

    Publishing an entry with zero data would create every entity in an unknown state and
    never recover, because the coordinator would consider itself healthy.
    """
    cloud.fail_next(FakeCloud.QUERY, httpx.ConnectError("down"), times=10)

    assert not await setup_integration()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


# --- authentication in the middle of normal operation ------------------------------------


async def test_token_expiring_between_polls_is_renewed_transparently(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """An expired token must cost one silent re-login, not an unavailable integration.

    Tokens expire on the cloud's schedule, usually while nobody is watching. Surfacing that
    as a failed update (or as reauth) would interrupt automations for no reason.
    """
    assert await setup_integration()
    logins_before = cloud.logins
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")

    cloud.rotate_token("TOKEN-AFTER-EXPIRY")
    cloud.set_attr(DISHWASHER_CODE, washingState="3")
    await _advance_one_poll(hass)

    assert cloud.logins == logins_before + 1
    assert hass.states.get(stage).state == "rinse"


async def test_a_command_rejected_for_a_stale_token_is_retried_after_relogin(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A control the user pressed must survive an expired token.

    Failing the first command after a token expiry — and only that one — is the kind of bug
    that looks like a flaky appliance and is never reproduced on demand.
    """
    assert await setup_integration()
    logins_before = cloud.logins
    cloud.rotate_token("TOKEN-AFTER-EXPIRY")

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": entity_id_of("switch", f"{DISHWASHER_CODE}_runState")},
        blocking=True,
    )

    assert cloud.logins == logins_before + 1
    assert cloud.instructions[-1] == (DISHWASHER_CODE, {"runState": "2"})


# --- capabilities ------------------------------------------------------------------------


# A model that reports none of the optional status keys: no salt tank, no rinse-aid
# dispenser, no consumption counters, no automatic door.
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


async def test_no_capability_source_at_all_hides_every_gated_entity(
    hass: HomeAssistant, cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """With no capability answer, no cache and nothing in the status, gated controls must
    not appear.

    Offering a control the appliance does not have is worse than offering nothing: the
    command is silently dropped and the entity lies about the result.
    """
    cloud.devices.clear()
    cloud.states.clear()
    cloud.add_dishwasher(state=BARE_STATE)
    cloud.fail_next(FakeCloud.CAPABILITY, httpx.ConnectError("down"), times=10)

    assert await setup_integration()

    assert entity_id_of("switch", f"{DISHWASHER_CODE}_runState") is not None  # ungated
    assert entity_id_of("switch", f"{DISHWASHER_CODE}_autoDoorOpen") is None
    assert entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear") is None
    assert entity_id_of("number", f"{DISHWASHER_CODE}_softWaterGear") is None
    assert entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_salt") is None


async def test_reported_status_keys_gate_entities_in_when_the_dictionary_is_down(
    hass: HomeAssistant, cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """An appliance that demonstrably reports a feature must keep its controls offline-safe.

    The capability dictionary is a separate service; when it is unavailable the only honest
    evidence left is what the appliance itself reports, and it must be enough.
    """
    cloud.fail_next(FakeCloud.CAPABILITY, httpx.ConnectError("down"), times=10)

    assert await setup_integration()

    assert entity_id_of("switch", f"{DISHWASHER_CODE}_autoDoorOpen") is not None
    assert entity_id_of("number", f"{DISHWASHER_CODE}_softWaterGear") is not None


async def test_a_feature_reported_for_the_first_time_creates_its_entities(
    hass: HomeAssistant, cloud: FakeCloud, setup_integration, entity_id_of
) -> None:
    """A key the appliance only reports later must bring its entities into existence.

    Some appliances omit optional groups from the status until the corresponding hardware is
    used once. Without this, a user who fills the salt container never gets the salt sensor
    until they reconfigure the integration by hand.
    """
    cloud.devices.clear()
    cloud.states.clear()
    cloud.add_dishwasher(state=BARE_STATE)
    cloud.fail_next(FakeCloud.CAPABILITY, httpx.ConnectError("down"), times=10)
    assert await setup_integration()
    assert entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_salt") is None

    cloud.set_attr(DISHWASHER_CODE, salt="1", softWaterGear="3")
    await _advance_one_poll(hass)
    await hass.async_block_till_done()

    assert entity_id_of("binary_sensor", f"{DISHWASHER_CODE}_salt") is not None
    assert entity_id_of("number", f"{DISHWASHER_CODE}_softWaterGear") is not None


async def test_a_cached_profile_carries_the_integration_through_a_cloud_outage(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Once resolved, capabilities must survive the cloud being unavailable at the next boot.

    Otherwise a restart during an outage silently deletes half the user's entities — and the
    automations referencing them.
    """
    assert await setup_integration()
    assert entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear") is not None

    entry = hass.config_entries.async_entries("holabrain")[0]
    cloud.fail_next(FakeCloud.CAPABILITY, httpx.ConnectError("down"), times=10)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear") is not None
    assert entity_id_of("switch", f"{DISHWASHER_CODE}_autoDoorOpen") is not None


async def test_a_shrinking_capability_answer_never_deletes_entities(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A truncated capability answer must not strip features that were already established.

    Capability responses are not always complete; treating a short answer as authoritative
    would remove entities (and break automations) at random.
    """
    assert await setup_integration()
    assert entity_id_of("number", f"{DISHWASHER_CODE}_softWaterGear") is not None

    entry = hass.config_entries.async_entries("holabrain")[0]
    cloud.capabilities["760EY179"] = ["rinse_aid", {"rinse_aid_gear": "5"}]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    # The salt-gated controls stay, because the appliance still reports the salt status key.
    assert entity_id_of("number", f"{DISHWASHER_CODE}_softWaterGear") is not None


async def test_gear_maximum_comes_from_the_model_not_from_the_category_default(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A model with fewer gears must not offer values it will reject.

    The category default is the widest range any model supports; publishing it for every
    model lets the user set a dose the appliance silently clamps.
    """
    cloud.capabilities["760EY179"] = ["rinse_aid", {"rinse_aid_gear": "3"}, "salt"]

    assert await setup_integration()

    state = hass.states.get(entity_id_of("number", f"{DISHWASHER_CODE}_distributorGear"))
    assert state.attributes["max"] == 3


# --- push channel unavailable ------------------------------------------------------------


async def test_no_push_certificate_falls_back_to_polling(
    hass: HomeAssistant,
    setup_integration,
    config_entry: MockConfigEntry,
    cloud: FakeCloud,
    mqtt_spy: MqttSpy,
    entity_id_of,
) -> None:
    """Push is an optimisation; losing it must cost latency, not the integration.

    The certificate endpoint is a separate service and fails independently of the rest of
    the cloud.
    """
    cloud.certificate = None

    assert await setup_integration()
    assert config_entry.state is ConfigEntryState.LOADED
    assert mqtt_spy.instances == []

    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    cloud.set_attr(DISHWASHER_CODE, washingState="5")
    await _advance_one_poll(hass)
    assert hass.states.get(stage).state == "finished"


async def test_broker_refusing_the_connection_falls_back_to_polling(
    hass: HomeAssistant,
    setup_integration,
    config_entry: MockConfigEntry,
    cloud: FakeCloud,
    mqtt_spy: MqttSpy,
    entity_id_of,
) -> None:
    """A broker that refuses the TLS connection must not fail the whole setup."""
    mqtt_spy.connect_error = OSError("connection refused")

    assert await setup_integration()
    assert config_entry.state is ConfigEntryState.LOADED

    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    cloud.set_attr(DISHWASHER_CODE, washingState="5")
    await _advance_one_poll(hass)
    assert hass.states.get(stage).state == "finished"


async def test_every_device_gets_its_own_push_subscription(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """Subscriptions are per device; missing one leaves that appliance on polling only.

    All three channels are subscribed, not just device frames: the heartbeat is what proves
    the push connection is still alive while an appliance sits idle, and that in turn is what
    lets the coordinator skip polling — which is what keeps it from competing for the
    account session.
    """
    second = "999900001111222"
    cloud.add_dishwasher(second, "Second Dishwasher")

    assert await setup_integration()

    subscriptions = set(mqtt_spy.last.subscriptions)
    for code in (DISHWASHER_CODE, second):
        assert {
            f"eu/eu_{code}/dev",
            f"eu/eu_{code}/will",
            f"eu/eu_{code}/hbt",
        } <= subscriptions
    assert len(subscriptions) == 6  # nothing else is subscribed


# --- optimistic updates ------------------------------------------------------------------


async def test_a_command_updates_the_entity_before_the_cloud_confirms(
    hass: HomeAssistant, setup_integration, entity_id_of
) -> None:
    """Controls must respond immediately, not after the next poll.

    Without the optimistic write the user sees the toggle snap back and presses it again,
    which sends the command twice.
    """
    assert await setup_integration()
    switch = entity_id_of("switch", f"{DISHWASHER_CODE}_runState")
    assert hass.states.get(switch).state == "on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch}, blocking=True
    )

    assert hass.states.get(switch).state == "off"


async def test_a_rejected_command_neither_updates_nor_hides_the_failure(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A command the cloud refuses must surface as an error and leave the state alone.

    An optimistic write applied before checking the response is the classic way a UI ends up
    showing a machine as running when the command never landed.
    """
    assert await setup_integration()
    switch = entity_id_of("switch", f"{DISHWASHER_CODE}_runState")
    cloud.fail_next(FakeCloud.COMMAND, {"code": 4321, "msg": "device busy"})

    with pytest.raises(Exception) as raised:
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch}, blocking=True
        )
    assert not isinstance(raised.value, AssertionError)

    assert hass.states.get(switch).state == "on"


async def test_a_failed_command_does_not_leave_a_broken_coordinator(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """After a rejected command the next poll must still work.

    A coordinator that marks itself failed on a command error takes every entity of the
    account down over one refused button press.
    """
    assert await setup_integration()
    switch = entity_id_of("switch", f"{DISHWASHER_CODE}_runState")
    cloud.fail_next(FakeCloud.COMMAND, ApiError("device busy", code=4321))

    with pytest.raises(ApiError):
        await hass.services.async_call(
            "switch", "turn_off", {"entity_id": switch}, blocking=True
        )

    cloud.set_attr(DISHWASHER_CODE, washingState="5")
    await _advance_one_poll(hass)
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    assert hass.states.get(stage).state == "finished"


def _coordinator(hass: HomeAssistant):
    entry = hass.config_entries.async_entries("holabrain")[0]
    return entry.runtime_data.coordinator
