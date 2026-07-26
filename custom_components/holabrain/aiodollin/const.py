"""Endpoints, regions and application constants for the HolaBrain (dollin) cloud API."""

from __future__ import annotations

from typing import Final

# Application-level identifiers used by the HolaBrain client. These are fixed constants of
# the cloud application (the same for every user of the ecosystem); they are not account
# secrets. Per-user tokens are obtained at login and stored via the TokenStore.
APP_ID: Final = "1020"
APP_KEY: Final = "meicloud"
APP_SECRET: Final = "PROD_VnoClJI9aikS8dyy"
ENCRYPT_KEY: Final = "4dbc9ff6c15944d78eebb581c2b23de3"
CLIENT_ID: Final = "8990073d748a1315c2ccdf47350d11f2"
CLIENT_SECRET: Final = "6dff9cfcd9366967bf451ebaa7e45a7d"

APP_VERSION: Final = "2.2.2"
SDK_VERSION: Final = 20230725
SIGNATURE_VERSION: Final = "2.0"

# Regional API hosts. RU/BY accounts live on `eu`, others on `us`.
REGION_HOSTS: Final[dict[str, str]] = {
    "eu": "https://eu.dollin.net",
    "us": "https://us.dollin.net",
}
DEFAULT_REGION: Final = "eu"

# ---- Endpoints ------------------------------------------------------------------------
# Legacy `/v1/*` endpoints use the OEM signature (sign + accessToken headers).
EP_LOGIN: Final = "/v1/user/login/new"
EP_AREA_GET: Final = "/v1/user/area/get"
EP_HOME_INDEX: Final = "/v1/appliance/user/home/index?order=1"
EP_CERT_CREATE: Final = "/v1/certificate/create/app/cert"
EP_PLUGIN_LATEST: Final = "/v1/product/upgrade/plugin/get/latest"
EP_FUNCTION_DICT: Final = "/v1/product/function/dict/get"

# Business `/midea/open/business/v1/*` endpoints use the ToB v2.0 signature.
EP_DEVICE_COMMAND: Final = "/midea/open/business/v1/device/command/{thing_code}"
EP_APPLIANCE_QUERY: Final = "/midea/open/business/v1/appliance/query/{thing_code}"

# Appliances report which command dialect they speak in ``thingProtocol``. Protocol 1 uses
# the endpoints above; the others exchange the same JSON instructions on a second pair of
# paths. Routing by the reported value keeps one client working across appliance families.
THING_PROTOCOL_DIRECT: Final = 1
EP_DEVICE_COMMAND_ALT: Final = (
    "/midea/open/business/v1/appliance/deviceCommands/requestNoReply/{thing_code}"
)
EP_APPLIANCE_QUERY_ALT: Final = (
    "/midea/open/business/v1/appliance/deviceCommands/query/{thing_code}"
)

# ---- Binding (adding / removing appliances on the account) -----------------------------
# Business endpoints use the ToB signature; the `/v1/*` ones use the OEM signature.
EP_BIND: Final = "/midea/open/business/v1/bind"
EP_UNBIND: Final = "/midea/open/business/v1/unbind"
EP_AUTH_STATUS: Final = "/midea/open/business/v1/appliance/auth/status"
EP_VERIFICATION: Final = "/midea/open/sdk/v2/appliance/verification"
EP_BINDER_REMOVE: Final = "/v1/appliance/binder/remove"
EP_NAME_UPDATE: Final = "/v1/appliance/name/update"
EP_LOCATION_SAVE: Final = "/v1/appliance/location/save"

# ---- Cloud push (MQTT) ----------------------------------------------------------------
MQTT_PORT: Final = 8883
# Topic layout: "<region>/<region>_<thing_code>/<channel>" where channel is dev/will/hbt.
MQTT_TOPIC_TEMPLATE: Final = "{region}/{region}_{thing_code}/{channel}"
MQTT_CHANNELS: Final = ("dev", "will", "hbt")

# Default HTTP timeout in seconds.
DEFAULT_TIMEOUT: Final = 25.0
