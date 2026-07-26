"""Home Assistant-backed session storage.

The cloud issues a long-lived account token at login. Keeping that token only in memory
means every Home Assistant restart performs a fresh login, which is both slow and needlessly
hard on the account — repeated logins are exactly the pattern a cloud throttles.

This store persists the session in the config entry, so a restart reuses the existing token
and only falls back to a login when the cloud actually rejects it.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .aiodollin import Session

STORAGE_KEY = "session"


class ConfigEntryTokenStore:
    """A :class:`~.aiodollin.TokenStore` that lives in the config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    async def load(self) -> Session | None:
        raw = self._entry.data.get(STORAGE_KEY)
        if not isinstance(raw, dict) or not raw.get("access_token"):
            return None
        return Session(
            access_token=str(raw["access_token"]),
            account=str(raw.get("account", "")),
            region=str(raw.get("region", "eu")),
            uid=str(raw.get("uid", "")),
        )

    async def save(self, session: Session) -> None:
        stored = {
            "access_token": session.access_token,
            "account": session.account,
            "region": session.region,
            "uid": session.uid,
        }
        if self._entry.data.get(STORAGE_KEY) == stored:
            return
        self._hass.config_entries.async_update_entry(
            self._entry, data={**self._entry.data, STORAGE_KEY: stored}
        )

    async def clear(self) -> None:
        if STORAGE_KEY not in self._entry.data:
            return
        data = dict(self._entry.data)
        data.pop(STORAGE_KEY, None)
        self._hass.config_entries.async_update_entry(self._entry, data=data)
