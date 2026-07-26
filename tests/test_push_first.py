"""Push-first polling: do not spend account requests while the push channel works.

The cloud allows one session per account, so every request Home Assistant makes can collide
with the vendor's mobile app: a request on a session the app has taken over forces a
re-login, which takes the session away from the app, which logs in again… The push channel
authenticates with its own certificate and is immune to that, so while it is delivering,
polling is skipped and the integration simply stops competing.

These tests assert on the number of cloud calls, because that is the thing that causes the
conflict.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL
from custom_components.holabrain.coordinator import PUSH_SILENCE_LIMIT
from tests.conftest import DISHWASHER_CODE, FakeCloud, MqttSpy

DEV_TOPIC = f"eu/eu_{DISHWASHER_CODE}/dev"
HEARTBEAT_TOPIC = f"eu/eu_{DISHWASHER_CODE}/hbt"


async def _poll(hass: HomeAssistant, times: int = 1) -> None:
    for index in range(times):
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=(DEFAULT_SCAN_INTERVAL + 5) * (index + 1)),
        )
        await hass.async_block_till_done()


async def test_a_live_push_channel_stops_the_polling(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """With push delivering, repeated poll ticks must not produce a single cloud request."""
    assert await setup_integration()
    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "2"}})
    await hass.async_block_till_done()

    before = cloud.calls(FakeCloud.QUERY)
    await _poll(hass, times=5)

    assert cloud.calls(FakeCloud.QUERY) == before


async def test_a_heartbeat_alone_is_enough_to_keep_polling_off(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """An idle appliance sends no state, only heartbeats — that must still count as alive.

    Otherwise every appliance that simply sits idle would drag the integration back into
    polling, which is exactly the state where the session conflict happens.
    """
    assert await setup_integration()
    mqtt_spy.last.deliver(HEARTBEAT_TOPIC, {"hbt": 1})
    await hass.async_block_till_done()

    before = cloud.calls(FakeCloud.QUERY)
    await _poll(hass, times=3)

    assert cloud.calls(FakeCloud.QUERY) == before


async def test_polling_resumes_when_push_goes_silent(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy, freezer
) -> None:
    """Skipping the poll must not turn into never refreshing again.

    If the push connection dies quietly — a dropped socket that never reconnects — the only
    way to notice a state change is to poll, so silence beyond the grace period brings
    polling back.
    """
    assert await setup_integration()
    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "2"}})
    await hass.async_block_till_done()

    before = cloud.calls(FakeCloud.QUERY)
    # Move real time forward too: silence is measured against the clock, not the timer.
    silence = PUSH_SILENCE_LIMIT + timedelta(minutes=1)
    freezer.move_to(dt_util.utcnow() + silence)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL))
    await hass.async_block_till_done()

    assert cloud.calls(FakeCloud.QUERY) > before


async def test_no_push_connection_means_ordinary_polling(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """When push cannot be established at all, the poll is the only source and must run."""
    mqtt_spy.connect_error = OSError("broker unreachable")
    assert await setup_integration()

    before = cloud.calls(FakeCloud.QUERY)
    await _poll(hass, times=2)

    assert cloud.calls(FakeCloud.QUERY) > before


async def test_state_still_tracks_the_appliance_without_polling(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """Skipping the poll is only acceptable because push carries the state itself."""
    assert await setup_integration()
    stage = "sensor.dishwasher_wash_stage"

    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "4"}})
    await hass.async_block_till_done()
    assert hass.states.get(stage).state == "drying"

    before = cloud.calls(FakeCloud.QUERY)
    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "3"}})
    await hass.async_block_till_done()

    assert hass.states.get(stage).state == "rinse"
    assert cloud.calls(FakeCloud.QUERY) == before  # updated without touching the account


# --- lifetime counters -------------------------------------------------------------------
#
# Push frames are a subset of the appliance's state: they leave out the lifetime water,
# energy and runtime counters, which appear only in the HTTP snapshot. Skipping the poll
# forever would therefore freeze those counters at whatever the first snapshot said. They
# move only when a cycle ends, so that is the one moment worth spending a request on.


async def test_a_finished_cycle_fetches_the_counters_push_leaves_out(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy, config_entry
) -> None:
    """After a wash ends the counters must reflect that wash, not the one before it."""
    assert await setup_integration()
    coordinator = config_entry.runtime_data.coordinator
    assert coordinator.data[DISHWASHER_CODE].attributes["totalWaterVol"] == "2900"

    # The appliance books the wash against its lifetime totals as the cycle settles.
    cloud.states[DISHWASHER_CODE].update(
        {"washingState": "5", "totalWaterVol": "2911", "totalElectricVol": "41058"}
    )

    before = cloud.calls(FakeCloud.QUERY)
    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "5"}})
    await hass.async_block_till_done()

    assert cloud.calls(FakeCloud.QUERY) > before
    attributes = coordinator.data[DISHWASHER_CODE].attributes
    assert attributes["totalWaterVol"] == "2911"
    assert attributes["totalElectricVol"] == "41058"


async def test_a_cycle_still_running_does_not_spend_a_request(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """The counters do not move mid-wash, so neither should the request budget.

    Without this the trigger would fire on every stage change and reintroduce exactly the
    polling the push-first design exists to avoid.
    """
    assert await setup_integration()
    before = cloud.calls(FakeCloud.QUERY)

    for stage in ("1", "2", "3", "4"):
        mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": stage}})
        await hass.async_block_till_done()

    assert cloud.calls(FakeCloud.QUERY) == before


async def test_the_counter_snapshot_is_taken_once_per_cycle(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, mqtt_spy: MqttSpy
) -> None:
    """One finished wash buys one snapshot — the request must not latch on."""
    assert await setup_integration()
    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "4"}})
    await hass.async_block_till_done()

    before = cloud.calls(FakeCloud.QUERY)
    # The snapshot agrees with the frame, as the cloud's does once the wash has settled.
    cloud.states[DISHWASHER_CODE]["washingState"] = "5"
    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "5"}})
    await hass.async_block_till_done()
    spent = cloud.calls(FakeCloud.QUERY) - before
    assert spent == 1

    # Settling further — finished to idle — is the same cycle, not a new one.
    cloud.states[DISHWASHER_CODE]["washingState"] = "0"
    mqtt_spy.last.deliver(DEV_TOPIC, {"status": {"washingState": "0"}})
    await hass.async_block_till_done()
    await _poll(hass, times=3)

    assert cloud.calls(FakeCloud.QUERY) == before + 1
