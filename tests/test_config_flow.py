"""Config flow: what happens when the user gets it wrong, twice, on the wrong continent.

The flow is the only place credentials are validated, so every test here targets a way it
can silently do the wrong thing: accept bad credentials, misreport *why* it failed, create a
half-broken entry, duplicate an account, or ignore the region and send the user's password
to the wrong host.
"""

from __future__ import annotations

import httpx
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.holabrain.aiodollin.auth.signer import encrypt_password
from custom_components.holabrain.aiodollin.const import ENCRYPT_KEY
from custom_components.holabrain.const import CONF_ACCOUNT, CONF_REGION, DOMAIN
from tests.conftest import FakeCloud


def _user_input(cloud: FakeCloud, **overrides) -> dict:
    data = {
        CONF_ACCOUNT: cloud.account,
        "password": cloud.password,
        CONF_REGION: "eu",
        "country": "RU",
    }
    data.update(overrides)
    return data


async def _submit(hass: HomeAssistant, user_input: dict):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_valid_credentials_create_an_entry_after_a_real_login(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """The flow must actually authenticate, not just accept whatever was typed.

    Catches the regression where validation is skipped (or swallowed) and an entry is
    created that only fails hours later during the first refresh.
    """
    cloud = patched_cloud
    result = await _submit(hass, _user_input(cloud))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == cloud.account
    assert result["data"][CONF_ACCOUNT] == cloud.account
    # The very first thing the flow did was authenticate — before creating anything.
    assert cloud.requests[0][0] == FakeCloud.LOGIN


async def test_password_is_encrypted_before_it_leaves_the_process(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """The login body must carry the encrypted password, never the plaintext.

    The fake cloud validates against the expected ciphertext, so a change to the encryption
    would fail the login; this test additionally pins that the plaintext is not on the wire.
    """
    cloud = patched_cloud
    await _submit(hass, _user_input(cloud))

    login_bodies = [payload for kind, _, payload in cloud.requests if kind == FakeCloud.LOGIN]
    assert login_bodies, "the flow never logged in"
    assert all(body["password"] != cloud.password for body in login_bodies)
    assert login_bodies[0]["password"] == encrypt_password(ENCRYPT_KEY, cloud.password)


async def test_wrong_password_shows_invalid_auth_and_creates_nothing(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """A rejected login must not leave a config entry behind.

    A half-created entry is the worst outcome: the user sees the integration installed and
    permanently broken, with no obvious way back to the login form.
    """
    cloud = patched_cloud
    result = await _submit(hass, _user_input(cloud, password="wrong"))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert hass.config_entries.async_entries(DOMAIN) == []


async def test_user_can_correct_the_password_inside_the_same_flow(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """Retrying in the same flow must succeed and must not carry the old error over.

    Flows that keep state between attempts (stale ``errors``, a cached client, a consumed
    unique id) break exactly here, and only for users who mistype once.
    """
    cloud = patched_cloud
    first = await _submit(hass, _user_input(cloud, password="wrong"))
    assert first["errors"] == {"base": "invalid_auth"}

    attempts_before_retry = cloud.logins
    second = await hass.config_entries.flow.async_configure(
        first["flow_id"], _user_input(cloud)
    )
    assert second["type"] is FlowResultType.CREATE_ENTRY
    # The retry really re-authenticated instead of reusing the rejected attempt.
    assert cloud.logins > attempts_before_retry


async def test_transport_failure_is_reported_as_cannot_connect(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """A dead network must not be shown to the user as "invalid password".

    Misclassifying connectivity as bad credentials sends people to reset a password that was
    never wrong.
    """
    cloud = patched_cloud
    cloud.fail_next(FakeCloud.LOGIN, httpx.ConnectError("no route to host"))

    result = await _submit(hass, _user_input(cloud))

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_unexpected_business_error_is_cannot_connect_not_invalid_auth(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """An unknown cloud error code must not be laundered into "invalid_auth"."""
    cloud = patched_cloud
    cloud.fail_next(FakeCloud.LOGIN, {"code": 9001, "msg": "service unavailable"})

    result = await _submit(hass, _user_input(cloud))

    assert result["errors"] == {"base": "cannot_connect"}


async def test_rate_limited_login_is_not_reported_as_invalid_auth(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """Throttling must not look like a wrong password.

    Repeatedly retrying a login while throttled is what gets an account locked, so the user
    has to be told to wait, not to re-type their password.
    """
    cloud = patched_cloud
    cloud.fail_next(FakeCloud.LOGIN, httpx.Response(429, text="slow down"))

    result = await _submit(hass, _user_input(cloud))

    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_account_in_different_case_is_rejected_as_duplicate(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """Account matching must be case-insensitive.

    "User@Example.com" and "user@example.com" are one account; letting both through
    duplicates every entity and doubles the polling load on the cloud.
    """
    cloud = patched_cloud
    first = await _submit(hass, _user_input(cloud))
    assert first["type"] is FlowResultType.CREATE_ENTRY

    cloud.account = cloud.account.upper()
    second = await _submit(hass, _user_input(cloud, account=cloud.account))

    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


@pytest.mark.parametrize(("region", "host"), [("eu", "eu.dollin.net"), ("us", "us.dollin.net")])
async def test_selected_region_decides_where_the_credentials_are_sent(
    hass: HomeAssistant, patched_cloud: FakeCloud, region: str, host: str
) -> None:
    """The region picker must reach the transport.

    If it is dropped somewhere between the form and the client, every account is validated
    against the default host — which succeeds for some users and fails inexplicably for the
    rest.
    """
    cloud = patched_cloud
    result = await _submit(hass, _user_input(cloud, region=region))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Both the validation login and everything the entry does afterwards go to one host.
    assert set(cloud.hosts) == {host}


async def test_flow_does_not_leak_the_validation_client_on_failure(
    hass: HomeAssistant, patched_cloud: FakeCloud
) -> None:
    """Every validation attempt must close its HTTP client, including the failing ones.

    A leaked client per mistyped password is invisible until a user with a bad connection
    retries a dozen times.
    """
    cloud = patched_cloud
    await _submit(hass, _user_input(cloud, password="wrong"))
    await _submit(hass, _user_input(cloud, password="still wrong"))

    assert len(cloud.owned_clients) == 2
    assert all(client.is_closed for client in cloud.owned_clients)


async def test_reconfigure_updates_the_credentials_in_place(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """A rotated password must be fixable without deleting the account.

    Re-adding the integration would take every entity's history and every automation
    reference with it, so reconfiguring has to update the entry itself — and it has to
    authenticate with the new credentials rather than replay the stored session.
    """
    assert await setup_integration()
    logins_before = cloud.logins

    cloud.password = "rotated-password"
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input(cloud, region="us")
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data["password"] == "rotated-password"
    assert config_entry.data[CONF_REGION] == "us"
    assert cloud.logins > logins_before


async def test_reconfigure_refuses_to_repoint_the_entry_at_another_account(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """Another account owns other appliances.

    Accepting one here would keep every device and entity of the old account while talking
    to a cloud that has never heard of them.
    """
    assert await setup_integration()

    result = await config_entry.start_reconfigure_flow(hass)
    cloud.account = "someone-else@example.com"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _user_input(cloud)
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "account_mismatch"
    assert config_entry.data[CONF_ACCOUNT] != cloud.account
