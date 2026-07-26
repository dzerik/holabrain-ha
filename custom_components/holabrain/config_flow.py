"""Config flow for HolaBrain — account login and integration options."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.network import async_get_ipv4_broadcast_addresses
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import HomeAssistant, callback

from .aiodollin import (
    AuthError,
    DollinClient,
    DollinError,
    InMemoryTokenStore,
    NotClaimable,
    SerialUnknown,
    async_discover,
    derive_verification_code,
)
from .aiodollin.api.binding import CODE_DEVICE_OFFLINE
from .aiodollin.exceptions import ApiError
from .const import (
    CONF_ACCOUNT,
    CONF_BSSID,
    CONF_PANEL,
    CONF_REGION,
    CONF_WIFI_PASSWORD,
    CONFIG_ENTRY_VERSION,
    DEFAULT_PANEL,
    DEFAULT_REGION,
    DOMAIN,
)
from .http_client import async_create_http_client
from .store import STORAGE_KEY

_LOGGER = logging.getLogger(__name__)

# Which of the appliances found on the network to claim.
CONF_APPLIANCE = "appliance"

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ACCOUNT): str,
        vol.Required("password"): str,
        vol.Required(CONF_REGION, default=DEFAULT_REGION): vol.In(["eu", "us"]),
        vol.Required("country", default="RU"): str,
    }
)


class HolabrainConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HolaBrain."""

    VERSION = CONFIG_ENTRY_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HolabrainOptionsFlow:
        """Return the options flow (panel toggle)."""
        return HolabrainOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_ACCOUNT].lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_ACCOUNT], data=user_input
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the stored credentials or region without losing the entities.

        Everything that can change lives here: a rotated password, an account that was
        migrated to the other region, a corrected country. The entry must keep pointing at
        the same account, though — a different one owns different appliances, and silently
        repointing an entry would rewrite every entity's device behind the user's back.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate(user_input)
            if not errors:
                await self.async_set_unique_id(user_input[CONF_ACCOUNT].lower())
                self._abort_if_unique_id_mismatch(reason="account_mismatch")
                # The stored session belongs to the old credentials; dropping it makes the
                # reload authenticate rather than replay a token that may now be wrong.
                data = {
                    key: value
                    for key, value in {**entry.data, **user_input}.items()
                    if key != STORAGE_KEY
                }
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA,
                {**entry.data, "password": ""},
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after the cloud rejected the stored session."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the password again and revalidate against the same account.

        The account, region and country are kept from the existing entry — only the
        credential is re-entered. The stored session is dropped so the reload authenticates
        with the new password instead of replaying the rejected token.
        """
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            candidate = {**entry.data, "password": user_input["password"]}
            errors = await self._async_validate(candidate)
            if not errors:
                data = {k: v for k, v in candidate.items() if k != STORAGE_KEY}
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("password"): str}),
            description_placeholders={"account": entry.data.get(CONF_ACCOUNT, "")},
            errors=errors,
        )

    async def _async_validate(self, user_input: dict[str, Any]) -> dict[str, str]:
        http = async_create_http_client()
        client = DollinClient.create(
            http,
            InMemoryTokenStore(),
            region=user_input[CONF_REGION],
            account=user_input[CONF_ACCOUNT],
            password=user_input["password"],
            country=user_input["country"],
        )
        try:
            await client.async_login()
        except AuthError:
            return {"base": "invalid_auth"}
        except DollinError:
            return {"base": "cannot_connect"}
        finally:
            await http.aclose()
        return {}


class HolabrainOptionsFlow(OptionsFlow):
    """Integration options: the optional sidebar panel and an on-demand account scan.

    The panel is opt-in because the integration is complete without it — every value and
    control it shows is a regular entity that any dashboard can use.

    Scanning is a separate step rather than something that happens on a timer, because it
    needs the account session and the cloud allows only one: taking it signs the vendor's
    mobile app out. The user is told that before it happens.
    """

    def __init__(self) -> None:
        self._candidates: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init", menu_options=["panel", "scan", "add_appliance"]
        )

    async def async_step_panel(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data={**self.config_entry.options, **user_input})
        current = self.config_entry.options.get(CONF_PANEL, DEFAULT_PANEL)
        return self.async_show_form(
            step_id="panel",
            data_schema=vol.Schema({vol.Required(CONF_PANEL, default=current): bool}),
        )

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then re-read the account inventory."""
        if user_input is None:
            return self.async_show_form(step_id="scan", data_schema=vol.Schema({}))

        coordinator = self.config_entry.runtime_data.coordinator
        try:
            added, removed = await coordinator.async_scan_devices()
        except AuthError:
            return self.async_abort(reason="scan_auth_failed")
        except Exception:
            # Anything else — a timeout, a broken response, a transport-level failure — is
            # the same story for the user: the scan did not happen, nothing was changed.
            _LOGGER.debug("account scan failed", exc_info=True)
            return self.async_abort(reason="scan_failed")
        return self.async_abort(
            reason="scan_done",
            description_placeholders={"added": str(added), "removed": str(removed)},
        )

    async def async_step_add_appliance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Look for appliances on the local network that are not on the account yet.

        Appliances answer a broadcast with their own serial, model and category, so nothing
        has to be read off a label — and because the id they report is the one the account
        uses, the ones already bound can be left out instead of being offered again.

        Claiming one only succeeds in a narrow window. An appliance offers itself to the
        cloud right after the mobile app has joined it to Wi-Fi; outside that window the
        cloud knows it but will not hand it over. Pressing the appliance's pairing button
        does not reopen the window — it clears the Wi-Fi configuration instead, taking the
        appliance off the network entirely (verified on hardware). So the realistic case for
        this step is an appliance the mobile app has just set up but failed to add, which
        does happen. Anything else has to go through the app.

        Nothing in the search touches the account session, so it cannot sign the mobile app
        out; only a claim does.
        """
        coordinator = self.config_entry.runtime_data.coordinator
        try:
            found = await async_discover(
                broadcast_addresses=await _async_broadcast_addresses(self.hass)
            )
        except OSError:
            _LOGGER.debug("local search failed", exc_info=True)
            return self.async_abort(reason="search_failed")

        self._candidates = {
            appliance.device_id: appliance
            for appliance in found
            if appliance.device_id not in coordinator.devices
        }
        if not self._candidates:
            return self.async_abort(
                reason="nothing_to_add" if found else "no_appliances_found"
            )
        return await self.async_step_claim()

    async def async_step_claim(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick one of the appliances found and claim it."""
        errors: dict[str, str] = {}
        entry = self.config_entry
        if user_input is not None:
            appliance = self._candidates.get(user_input[CONF_APPLIANCE])
            if appliance is None:
                errors["base"] = "not_claimable"
            else:
                result = await self._async_claim(appliance, user_input)
                if isinstance(result, str):
                    errors["base"] = result
                else:
                    return result

        labels = {
            device_id: f"{appliance.model} · {appliance.host}"
            for device_id, appliance in self._candidates.items()
        }
        return self.async_show_form(
            step_id="claim",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_APPLIANCE, default=next(iter(labels))): vol.In(labels),
                    vol.Optional(
                        CONF_BSSID, default=entry.data.get(CONF_BSSID, "")
                    ): str,
                    vol.Optional(
                        CONF_WIFI_PASSWORD, default=entry.data.get(CONF_WIFI_PASSWORD, "")
                    ): str,
                }
            ),
            errors=errors,
        )

    async def _async_claim(
        self, appliance, user_input: dict[str, Any]
    ) -> ConfigFlowResult | str:
        """Claim one appliance; returns an error key instead of raising."""
        entry = self.config_entry
        client = entry.runtime_data.client
        bssid = user_input.get(CONF_BSSID, "").strip()
        password = user_input.get(CONF_WIFI_PASSWORD, "")
        # The Wi-Fi details are optional: the cloud returns the appliance's own verification
        # code in its answer, and what we send appears to be a hint rather than a secret.
        # When they are given we compute the code the way the appliance itself would, which
        # is the only form guaranteed to be accepted.
        code = (
            derive_verification_code(bssid, password) if bssid else "0" * 32
        )
        try:
            offered = await client.binding.async_find(appliance.serial, code)
        except SerialUnknown:
            return "serial_unknown"
        except NotClaimable:
            # Without the Wi-Fi details we cannot tell "not in setup mode" from "the code we
            # guessed was not accepted", so the hint says to try adding them.
            return "not_claimable" if bssid else "not_claimable_try_wifi"
        except Exception:
            _LOGGER.debug("appliance lookup failed", exc_info=True)
            return "cannot_connect"
        if not offered:
            return "not_claimable" if bssid else "not_claimable_try_wifi"

        try:
            await client.binding.async_bind(
                offered[0].appliance_code,
                offered[0].verification_code,
                appliance.device_type,
                time_zone_id=str(self.hass.config.time_zone or ""),
            )
        except ApiError as err:
            return "appliance_offline" if err.code == CODE_DEVICE_OFFLINE else "bind_failed"
        except Exception:
            _LOGGER.debug("bind failed", exc_info=True)
            return "bind_failed"

        # Remember the network so the next appliance does not have to be typed in again.
        if bssid:
            self.hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_BSSID: bssid, CONF_WIFI_PASSWORD: password},
            )
        added, _ = await entry.runtime_data.coordinator.async_scan_devices()
        return self.async_abort(
            reason="appliance_added", description_placeholders={"added": str(added)}
        )


async def _async_broadcast_addresses(hass: HomeAssistant) -> tuple[str, ...]:
    """Broadcast targets for the local search.

    The global address alone is dropped by some routers, so each of the host's own IPv4
    networks is probed as well — a host on several segments would otherwise only ever see
    one of them.
    """
    addresses = ["255.255.255.255"]
    try:
        addresses += [
            str(address) for address in await async_get_ipv4_broadcast_addresses(hass)
        ]
    except Exception:
        _LOGGER.debug("could not enumerate local networks", exc_info=True)
    return tuple(dict.fromkeys(addresses))
