"""Optional built-in frontend panel and dashboard card assets.

The frontend in ``www/`` is a plain Home Assistant client: it reads entity states and
calls services, exactly like a dashboard would. It never talks to the cloud API, so
enabling it adds no traffic and no extra failure mode.

Two things are registered here, deliberately decoupled:

* the **static path** ``/holabrain_panel`` — mounted whenever the integration is loaded,
  because it also serves the Lovelace card users add as a dashboard resource;
* the **sidebar panel** — opt-in through the integration options, so an installation that
  only wants entities keeps its sidebar untouched.
"""

from __future__ import annotations

import contextlib
import logging
import pathlib

from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    PANEL_ICON,
    PANEL_MODULE,
    PANEL_STATIC_PATH,
    PANEL_TITLE,
    PANEL_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)

# hass.data markers keep both registrations idempotent across config entries.
_STATIC_MARKER = f"{DOMAIN}_static_registered"
_PANEL_MARKER = f"{DOMAIN}_panel_registered"


async def async_register_static_path(hass: HomeAssistant) -> str | None:
    """Mount ``www/`` once and return the cache-busted URL of the panel module.

    Returns ``None`` when the HTTP component is unavailable — there is then nothing to
    serve the assets from, and the panel is skipped rather than registered broken.

    The integration version is used as the cache buster, so a user who upgrades gets the
    new frontend without a hard refresh.
    """
    if getattr(hass, "http", None) is None:
        _LOGGER.debug("HTTP component unavailable; frontend assets are not served")
        return None
    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version or "0"
    if not hass.data.get(_STATIC_MARKER):
        panel_dir = str(pathlib.Path(__file__).parent / "www")
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_PATH, panel_dir, cache_headers=False)]
        )
        # Static paths cannot be unmounted; the marker stays set for the lifetime of the
        # process so a reload never registers the same path twice.
        hass.data[_STATIC_MARKER] = True
    return f"{PANEL_STATIC_PATH}/{PANEL_MODULE}?v={version}"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Add the sidebar panel (no-op when it is already there)."""
    module_url = await async_register_static_path(hass)
    if module_url is None or hass.data.get(_PANEL_MARKER):
        return
    async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=PANEL_URL_PATH,
        config={
            "_panel_custom": {
                "name": "holabrain-panel",
                "module_url": module_url,
            }
        },
        require_admin=False,
    )
    hass.data[_PANEL_MARKER] = True


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar panel (no-op when it is not registered)."""
    if not hass.data.pop(_PANEL_MARKER, None):
        return
    with contextlib.suppress(KeyError, ValueError):
        async_remove_panel(hass, PANEL_URL_PATH)
