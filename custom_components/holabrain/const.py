"""Constants for the HolaBrain Home Assistant integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "holabrain"

# Schema version of the config entry. Owned here rather than by the config flow so the
# migration hook and the flow can never disagree about what "current" means.
CONFIG_ENTRY_VERSION = 1

# Platforms are generic and driven by registry.py; a platform with no entities for the
# installed devices simply sets up nothing.
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

# Config entry data keys.
CONF_ACCOUNT = "account"
CONF_REGION = "region"

DEFAULT_REGION = "eu"

# Default polling fallback interval (seconds) used alongside cloud push.
DEFAULT_SCAN_INTERVAL = 60

# Config entry option: show the built-in panel in the sidebar. Off by default — the
# integration is fully usable through plain entities and dashboards.
# Remembered so the next appliance does not have to be typed in again. The password is a
# credential, and lives beside the account password already stored on the entry.
CONF_BSSID = "bssid"
CONF_WIFI_PASSWORD = "wifi_password"

CONF_PANEL = "panel"
DEFAULT_PANEL = False

# Frontend assets. The static path also serves the Lovelace card, so it is mounted
# whenever the integration is loaded, independently of the panel option.
PANEL_URL_PATH = "holabrain"
PANEL_STATIC_PATH = "/holabrain_panel"
PANEL_MODULE = "holabrain-panel.js"
PANEL_TITLE = "HolaBrain"
PANEL_ICON = "mdi:dishwasher"
