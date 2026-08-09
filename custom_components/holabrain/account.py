"""Account-level entities: the controls that belong to the connection, not to an appliance.

The cloud allows exactly one session per account. Whoever logs in last holds it, and the
loser finds out by getting an error on its next request. There is no way to share it, so
the integration cannot silently decide whether Home Assistant or the phone in the user's
pocket should win — it has to be asked, and it has to be easy to change your mind.

That is what lives here: a switch for the mode, a button for the one-off refresh that makes
cooperative mode practical, and a button that mints a new session when the current one is
wedged.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MODE_COOPERATIVE, MODE_EXCLUSIVE
from .coordinator import HolabrainCoordinator


def account_device_info(entry_id: str) -> DeviceInfo:
    """The service device standing for the cloud account itself.

    Its entities describe the *connection*, so hanging them off an appliance would be a
    lie — and would disappear the moment that appliance was removed.
    """
    return DeviceInfo(
        identifiers={(DOMAIN, f"account_{entry_id}")},
        # Deliberately not the account's e-mail address, which is what the config entry is
        # titled with: Home Assistant builds entity ids from the device name, and
        # "switch.name_example_com_exclusive_mode" would put the address in every automation,
        # every log line and every screenshot attached to a bug report.
        name="HolaBrain",
        manufacturer="HolaBrain",
        entry_type=DeviceEntryType.SERVICE,
    )


class HolabrainAccountEntity(CoordinatorEntity[HolabrainCoordinator]):
    """Base for entities that belong to the account rather than to an appliance."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HolabrainCoordinator, uid: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{uid}"
        self._attr_device_info = account_device_info(entry.entry_id)

    @property
    def available(self) -> bool:
        # Deliberately always available. These controls exist to get the integration *out*
        # of a bad state, so they must not disappear when the cloud is unreachable — which
        # is exactly when someone wants to reach for them.
        return True


class HolabrainExclusiveModeSwitch(HolabrainAccountEntity, SwitchEntity):
    """Take the account over, or share it with the mobile app.

    On means Home Assistant is the primary client: it polls, fetches consumption
    statistics and reclaims the session whenever something else takes it — which signs the
    mobile app out, repeatedly. Off means the integration never spends an account request
    on its own initiative and lives on the push channel, which uses its own certificate and
    leaves the session alone.
    """

    _attr_translation_key = "exclusive_mode"
    _attr_entity_category = None  # a deliberate choice the user makes, not a setting

    def __init__(self, coordinator: HolabrainCoordinator) -> None:
        super().__init__(coordinator, "exclusive_mode")

    @property
    def is_on(self) -> bool:
        return self.coordinator.mode == MODE_EXCLUSIVE

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"mode": self.coordinator.mode}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_mode(MODE_EXCLUSIVE)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_mode(MODE_COOPERATIVE)


class HolabrainRefreshButton(HolabrainAccountEntity, ButtonEntity):
    """Fetch the appliances' status once, right now.

    This is what makes cooperative mode usable: the integration will not poll by itself,
    but a person can always ask. Pressing it costs the mobile app its session for as long
    as it takes the app to log back in.
    """

    _attr_translation_key = "refresh_now"

    def __init__(self, coordinator: HolabrainCoordinator) -> None:
        super().__init__(coordinator, "refresh_now")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_now()


class HolabrainRefreshTokenButton(HolabrainAccountEntity, ButtonEntity):
    """Sign in again and replace the account token.

    The integration replaces an expired token on its own, so this is not part of normal
    operation — it is the way out of a session the cloud keeps refusing. Disabled by
    default because pressing it claims the account's only session, and losing the mobile
    app's session is not something a stray tap should do.
    """

    _attr_translation_key = "refresh_token"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: HolabrainCoordinator) -> None:
        super().__init__(coordinator, "refresh_token")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_token()
