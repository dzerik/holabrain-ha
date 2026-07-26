"""What happens when the world misbehaves: bad credentials, outages, restarts, removal.

Every test here describes a situation a user will eventually hit and that is invisible in a
happy-path suite:

* the account password is changed in the vendor app while Home Assistant keeps polling;
* the vendor app claims the account's single session, which looks like an auth failure but
  must never be one;
* the cloud goes away for longer than a hiccup;
* the setup fails after the push connection was already opened;
* an account is deleted and leaves credentials and a private key behind.
"""

from __future__ import annotations

import os
import ssl
import stat
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.holabrain.aiodollin import SessionTakeoverError
from custom_components.holabrain.const import DEFAULT_SCAN_INTERVAL, DOMAIN
from custom_components.holabrain.coordinator import (
    CAPABILITY_STORAGE_KEY,
    POLL_FAILURE_GRACE,
    _write_private,
    cert_file_paths,
)
from tests.conftest import DISHWASHER_CODE, FakeCloud, MqttSpy


async def _advance_one_poll(hass: HomeAssistant) -> None:
    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=DEFAULT_SCAN_INTERVAL + 5)
    )
    await hass.async_block_till_done()


def _reauth_flows(hass: HomeAssistant) -> list[dict]:
    return [
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["handler"] == DOMAIN and flow["context"]["source"] == SOURCE_REAUTH
    ]


# --- credentials that stop working -------------------------------------------------------


async def test_a_password_changed_elsewhere_asks_the_user_to_sign_in_again(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Changing the account password in the vendor app must surface as re-authentication.

    Otherwise the integration keeps failing every poll with a debug-level message, entities
    freeze on their last value and the only symptom the user gets is "it stopped updating".
    """
    assert await setup_integration()
    assert not _reauth_flows(hass)

    # The password was changed elsewhere: the stored session is invalidated and the
    # credentials this entry holds are no longer accepted.
    cloud.rotate_token("TOKEN-AFTER-PASSWORD-CHANGE")
    cloud.password = "the-new-one"
    await _advance_one_poll(hass)

    assert _reauth_flows(hass), "the user was never asked to re-authenticate"


async def test_the_app_holding_the_session_never_asks_for_the_password(
    hass: HomeAssistant, setup_integration, config_entry: MockConfigEntry, entity_id_of
) -> None:
    """A session taken over by the vendor app is not an authentication problem.

    The credentials are fine and there is nothing for the user to type; the auth manager
    reclaims the session on its own. Prompting for a password here would train users to
    re-enter it every time they open the mobile app — and every re-entry logs the app out.
    """
    assert await setup_integration()
    client = config_entry.runtime_data.client

    async def _taken(thing_code: str):
        raise SessionTakeoverError("the account session is in use by another client")

    with patch.object(client.devices, "async_get_state", _taken):
        await _advance_one_poll(hass)

        assert not _reauth_flows(hass)
        assert config_entry.state is ConfigEntryState.LOADED
        # The last known state is still shown: the appliance itself is fine.
        stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
        assert hass.states.get(stage).state == "main_wash"


# --- the cloud going away ----------------------------------------------------------------


async def test_a_cloud_that_stays_down_stops_pretending_the_data_is_current(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """A sustained outage has to become visible instead of freezing old values.

    A single failed cycle is tolerated (the cloud answers one query with an error several
    times a day), but an outage that lasts must mark the entities unavailable — an
    automation acting on hours-old values it believes are current is the worst outcome here.
    """
    assert await setup_integration()
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    cloud.fail_next(FakeCloud.QUERY, httpx.ConnectError("network is unreachable"), times=20)

    for _ in range(POLL_FAILURE_GRACE):
        await _advance_one_poll(hass)
        assert hass.states.get(stage).state == "main_wash"

    await _advance_one_poll(hass)
    assert hass.states.get(stage).state == "unavailable"


async def test_entities_come_back_by_themselves_once_the_cloud_returns(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud, entity_id_of
) -> None:
    """Recovery must need no user action — outages end while nobody is watching."""
    assert await setup_integration()
    stage = entity_id_of("sensor", f"{DISHWASHER_CODE}_washingState")
    cloud.fail_next(FakeCloud.QUERY, httpx.ConnectError("down"), times=POLL_FAILURE_GRACE + 1)

    for _ in range(POLL_FAILURE_GRACE + 1):
        await _advance_one_poll(hass)
    assert hass.states.get(stage).state == "unavailable"

    cloud.set_attr(DISHWASHER_CODE, washingState="3")
    await _advance_one_poll(hass)

    assert hass.states.get(stage).state == "rinse"


# --- setup that fails halfway ------------------------------------------------------------


async def test_a_setup_that_fails_late_takes_its_push_connection_with_it(
    hass: HomeAssistant,
    setup_integration,
    config_entry: MockConfigEntry,
    cloud: FakeCloud,
    mqtt_spy: MqttSpy,
) -> None:
    """A first refresh that fails after the push channel is up must not leak it.

    The inventory and the push certificate come from different endpoints, so a cloud that
    answers one and not the other is a real state. Home Assistant retries the setup every
    30 seconds, and each attempt would otherwise leave behind a TLS connection, a network
    thread and a revalidation timer.
    """
    cloud.fail_next(FakeCloud.QUERY, httpx.ConnectError("status service is down"), times=10)

    assert not await setup_integration()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert mqtt_spy.instances, "the push connection was never opened; test is not exercising"
    assert all(not broker.connected for broker in mqtt_spy.instances)
    assert mqtt_spy.last.disconnect_calls == 1


# --- credentials and key material on disk ------------------------------------------------


async def test_removing_the_account_takes_its_stored_files_with_it(
    hass: HomeAssistant,
    setup_integration,
    config_entry: MockConfigEntry,
    hass_storage: dict,
) -> None:
    """Deleting the entry must delete the capability cache and the push key material.

    Both live in ``.storage`` under the entry id, so nothing would ever match them again;
    the private key in particular must not outlive the account it authenticates.
    """
    assert await setup_integration()
    storage_key = f"{CAPABILITY_STORAGE_KEY}_{config_entry.entry_id}"
    assert storage_key in hass_storage

    cert_path, key_path = cert_file_paths(hass, config_entry.entry_id)
    for path in (cert_path, key_path):
        await hass.async_add_executor_job(_write_private, path, "material")

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()

    assert storage_key not in hass_storage
    assert not os.path.exists(cert_path)
    assert not os.path.exists(key_path)


def test_the_push_private_key_is_not_readable_by_other_users(tmp_path) -> None:
    """The minted client certificate is key material: anything that can read it can

    impersonate this installation on the push channel until the certificate expires. Home
    Assistant's configuration directory is not a secret store, so the mode is set here.
    """
    path = str(tmp_path / "push.key")
    # A file left over from an older version, written with the default mode.
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("old and world readable")
    os.chmod(path, 0o644)

    _write_private(path, "-----BEGIN PRIVATE KEY-----")

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with open(path, encoding="utf-8") as handle:
        assert handle.read() == "-----BEGIN PRIVATE KEY-----"


# --- blocking work that must not happen in the event loop --------------------------------


@pytest.mark.parametrize("blocked", ["load_verify_locations", "load_default_certs"])
async def test_no_tls_trust_store_is_read_while_the_event_loop_runs(
    hass: HomeAssistant, setup_integration, blocked: str
) -> None:
    """Setting up must not build a TLS context from disk on the event loop.

    ``httpx.AsyncClient()`` reads the CA bundle while it is constructed. Doing that in the
    loop is blocking I/O that Home Assistant reports as an integration bug, and it would
    happen on every setup, every reload and every config-flow step. Home Assistant keeps a
    pre-warmed context for exactly this; this test fails the moment the client is created
    the naive way again.
    """

    def _explode(*args, **kwargs):
        raise AssertionError(f"{blocked} was called from the event loop")

    with patch.object(ssl.SSLContext, blocked, _explode):
        assert await setup_integration()


# --- entries written by another version --------------------------------------------------


async def test_an_entry_from_a_newer_version_is_refused_rather_than_misread(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """Downgrading the integration must not let it load data it cannot interpret.

    Home Assistant offers no downgrade protection by itself: without a migration hook that
    says no, an entry written by a future schema is loaded as if it were current, and the
    first thing the user notices is entities behaving oddly rather than an error.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=99,
        title=patched_cloud.account,
        data={
            "account": patched_cloud.account,
            "password": patched_cloud.password,
            "region": "eu",
            "country": "RU",
        },
    )
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR


# --- naming ------------------------------------------------------------------------------


async def test_an_appliance_the_account_never_named_still_reads_well(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """An unnamed appliance must not produce entities called " door".

    The account returns an empty name for an appliance that was added but never renamed in
    the vendor app. With ``has_entity_name`` the device name is the prefix of every entity
    name, so an empty one is visible everywhere at once.
    """
    cloud.devices[0]["thingName"] = ""
    assert await setup_integration()

    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DISHWASHER_CODE)})
    assert device is not None
    assert device.name == "Dishwasher"


async def test_a_throttled_cloud_is_not_hammered_for_the_rest_of_the_cycle(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """Rate limiting applies to the account, not to one appliance.

    Continuing to poll the remaining appliances after a throttling answer only extends the
    penalty; the cycle stops instead and the next one tries again a minute later.
    """
    cloud.add_dishwasher("999900001111222", "Second Dishwasher")
    cloud.add_dishwasher("999900001111333", "Third Dishwasher")
    assert await setup_integration()

    before = cloud.calls(FakeCloud.QUERY)
    cloud.fail_next(FakeCloud.QUERY, httpx.Response(429, json={"code": 429, "msg": "slow down"}))
    await _advance_one_poll(hass)

    assert cloud.calls(FakeCloud.QUERY) - before == 1
