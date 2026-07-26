"""Cooperative and exclusive mode: who gets the account's single session.

The cloud allows one session per account. Every request Home Assistant makes can evict
whoever holds it — in practice the phone in the user's pocket — and the evicted client logs
straight back in, taking it away again. There is no way to share it, only a choice about who
wins, so the integration asks instead of deciding.

These tests assert on the number of cloud requests, because the request *is* the conflict.
"""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.holabrain.const import (
    CONF_MODE,
    DEFAULT_MODE,
    DEFAULT_SCAN_INTERVAL,
    MODE_COOPERATIVE,
    MODE_EXCLUSIVE,
)
from tests.conftest import DISHWASHER_CODE, FakeCloud

DEV_TOPIC = f"eu/eu_{DISHWASHER_CODE}/dev"


async def _poll(hass: HomeAssistant, times: int = 1) -> None:
    for index in range(times):
        async_fire_time_changed(
            hass,
            dt_util.utcnow() + timedelta(seconds=(DEFAULT_SCAN_INTERVAL + 5) * (index + 1)),
        )
        await hass.async_block_till_done()


def test_a_fresh_install_shares_the_account() -> None:
    """The default has to be the polite one.

    A new user still has the vendor app installed. Signing them out of it within minutes of
    adding the integration, with no warning and no obvious cause, is the worst possible
    first impression — and the hardest kind of problem for them to attribute to us.
    """
    assert DEFAULT_MODE == MODE_COOPERATIVE


async def test_cooperative_mode_never_polls_on_its_own(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, config_entry
) -> None:
    """Not once, not even with the push channel silent.

    Push-first already skips the poll while push delivers. Cooperative mode is the stronger
    promise: silence is not an excuse to start competing for the account either, because the
    user asked for their app to keep working.
    """
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MODE: MODE_COOPERATIVE}
    )
    assert await setup_integration()

    before = cloud.calls(FakeCloud.QUERY)
    await _poll(hass, times=5)

    assert cloud.calls(FakeCloud.QUERY) == before


async def test_the_first_refresh_runs_whatever_the_mode(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, config_entry, entity_id_of
) -> None:
    """Setting the integration up *is* the user asking for the data.

    Refusing the very first poll would leave every entity unknown for ever, with no way for
    the user to tell a polite integration from a broken one.
    """
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MODE: MODE_COOPERATIVE}
    )
    assert await setup_integration()

    assert cloud.calls(FakeCloud.QUERY) >= 1
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    assert hass.states.get(stage).state == "main_wash"


async def test_the_refresh_button_polls_even_in_cooperative_mode(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, config_entry, entity_id_of
) -> None:
    """Cooperative forbids *our* initiative, not the user's.

    Without this the mode would be a dead end: no polling, no way to ask, and a device page
    that quietly ages whenever push drops.
    """
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MODE: MODE_COOPERATIVE}
    )
    assert await setup_integration()
    refresh = entity_id_of("button", f"{config_entry.entry_id}_refresh_now")

    before = cloud.calls(FakeCloud.QUERY)
    await hass.services.async_call(
        "button", "press", {"entity_id": refresh}, blocking=True
    )
    await hass.async_block_till_done()

    assert cloud.calls(FakeCloud.QUERY) > before


async def test_switching_to_exclusive_takes_effect_without_a_reload(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, config_entry, entity_id_of
) -> None:
    """Flipping the switch has to change behaviour there and then.

    Requiring a reload would tear down the push connection and re-create the certificate for
    what is a one-word option — and would make the switch feel broken.
    """
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MODE: MODE_COOPERATIVE}
    )
    assert await setup_integration()
    mode_switch = entity_id_of("switch", f"{config_entry.entry_id}_exclusive_mode")
    assert hass.states.get(mode_switch).state == "off"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": mode_switch}, blocking=True
    )
    await hass.async_block_till_done()

    assert hass.states.get(mode_switch).state == "on"
    assert config_entry.options[CONF_MODE] == MODE_EXCLUSIVE

    before = cloud.calls(FakeCloud.QUERY)
    await _poll(hass, times=2)
    assert cloud.calls(FakeCloud.QUERY) > before


async def test_the_account_controls_survive_an_appliance_going_offline(
    hass: HomeAssistant, setup_integration, push, config_entry, entity_id_of
) -> None:
    """They describe the connection, not the appliance.

    An outage is exactly when someone reaches for "refresh" or wants to hand the account
    back to the app, so these two must never be the entities that grey out.
    """
    assert await setup_integration()
    mode_switch = entity_id_of("switch", f"{config_entry.entry_id}_exclusive_mode")
    refresh = entity_id_of("button", f"{config_entry.entry_id}_refresh_now")

    await push(DEV_TOPIC, {"onlineChange": {"online": 0}})

    assert hass.states.get(mode_switch).state != "unavailable"
    assert hass.states.get(refresh).state != "unavailable"


async def test_a_command_is_sent_in_cooperative_mode(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, config_entry, entity_id_of
) -> None:
    """Writes are the user acting, so they are never withheld.

    Cooperative mode is about not *polling*; an integration that silently declined to switch
    the appliance on would be broken, not polite.
    """
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, CONF_MODE: MODE_COOPERATIVE}
    )
    assert await setup_integration()
    power = entity_id_of("switch", f"{DISHWASHER_CODE}_power")

    before = len(cloud.instructions)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": power}, blocking=True
    )
    await hass.async_block_till_done()

    assert len(cloud.instructions) > before
