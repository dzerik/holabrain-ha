"""Shared fixtures for the Home Assistant side of the integration.

The whole suite runs against an in-memory fake of the cloud API wired in at the httpx layer
(``httpx.MockTransport``) rather than by stubbing :class:`DollinClient`. That way every HA
test also exercises the real transport, request signing, auth retry, DTO parsing and
registry code — a stubbed client would hide exactly the failures that hurt in production
(wrong payload shape, business-code handling, token refresh).

Nothing here opens a socket: ``FakeCloud`` answers requests synchronously and
``FakeMqttBroker`` replaces the cloud push connection.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.holabrain.aiodollin import DollinClient, InMemoryTokenStore
from custom_components.holabrain.aiodollin.auth.signer import encrypt_password
from custom_components.holabrain.aiodollin.const import ENCRYPT_KEY
from custom_components.holabrain.const import CONF_ACCOUNT, CONF_REGION, DOMAIN
from custom_components.holabrain.coordinator import HolabrainCoordinator

# --- device fixtures ---------------------------------------------------------------------
# Shapes copied from real account payloads: the device list uses camelCase keys, reports
# `online` as an int, and `sn8` (not `model`) is the capability lookup key.

DISHWASHER_CODE = "100000000000001"
LAMP_CODE = "220011223344556"
BOILER_CODE = "330011223344556"
AC_CODE = "440011223344556"

DISHWASHER_MODEL = "760EY179"
LAMP_MODEL = "LAMP0001"
BOILER_MODEL = "BOIL0001"
AC_MODEL = "AIRC0001"

# A full dishwasher capability list: bare tokens, single-key parameter dicts and gear caps.
DISHWASHER_CAPABILITY: list[Any] = [
    "rinse_aid",
    {"rinse_aid_gear": "5"},
    "salt",
    {"salt_gear": "6"},
    "statistics",
    "auto_open",
]

DISHWASHER_STATE: dict[str, Any] = {
    "power": "3",
    "runState": "1",
    "washingState": "2",
    "modeEU": "4",
    "faultCode": "0",
    "doorstatus": "1",
    "realTemp": "58",
    "remainTimeH": "0",
    "remainTimeL": "95",
    "salt": "0",
    "brightenAgent": "0",
    "autoDoorOpen": "2",
    "distributorGear": "3",
    "softWaterGear": "4",
    "saltTimes": "12",
    "brightenAgentTimes": "7",
    "totalwashTimes": "310",
    "totalWaterVol": "2900",
    "totalElectricVol": "41000",
    "online": 1,
}

LAMP_STATE: dict[str, Any] = {"power": "1", "bright": "128", "colorTemp": "0", "mode": "3"}
BOILER_STATE: dict[str, Any] = {
    "power": "1",
    "temp": "60",
    "cur_temperature": "48",
    "eco": "0",
    "cloudSmart": "0",
    "highTemp": "0",
    "heatStatus": "1",
}
AC_STATE: dict[str, Any] = {
    "power": "1",
    "mode": "2",
    "temp": "23",
    "indoorTemp": "27",
    "windSpeed": "60",
}


def device_entry(
    thing_code: str, name: str, device_type: str, model: str, *, online: int = 1
) -> dict[str, Any]:
    """One row of the account device list."""
    return {
        "thingCode": thing_code,
        "thingName": name,
        "deviceType": device_type,
        "firmwareVersion": "059006092306",
        "model": model,
        "sn8": model,
        "online": online,
        "pluginType": 1,
    }


# --- fake cloud --------------------------------------------------------------------------


class FakeCloud:
    """In-memory stand-in for the vendor cloud, served through ``httpx.MockTransport``.

    Requests are routed by path, checked for a current access token and answered from
    mutable state the test owns. Failures are scripted per endpoint kind with
    :meth:`fail_next`, so a test can say "the next status poll times out" instead of
    monkeypatching integration internals.
    """

    LOGIN = "login"
    DEVICES = "devices"
    CAPABILITY = "capability"
    CERTIFICATE = "certificate"
    QUERY = "query"
    COMMAND = "command"
    VERIFICATION = "verification"
    BIND = "bind"
    RENAME = "rename"
    UNBIND = "unbind"

    def __init__(self) -> None:
        self.devices: list[dict[str, Any]] = []
        self.states: dict[str, dict[str, Any]] = {}
        self.capabilities: dict[str, list[Any]] = {}
        self.certificate: dict[str, Any] | None = {
            "privateKey": "-----BEGIN PRIVATE KEY-----\nfake\n-----END PRIVATE KEY-----\n",
            "certificatePem": "-----BEGIN CERTIFICATE-----\nfake\n-----END CERTIFICATE-----\n",
            "endpoint": "push.example.invalid",
            "port": 8883,
        }
        self.account = "user@example.com"
        self.password = "hunter2"
        self.token = "TOKEN-1"
        self.logins = 0
        self.requests: list[tuple[str, str, Any]] = []
        self.hosts: list[str] = []
        self.instructions: list[tuple[str, dict[str, Any]]] = []
        # httpx clients the integration created for itself; used to assert they get closed.
        self.owned_clients: list[httpx.AsyncClient] = []
        self._scripted: dict[str, deque[Any]] = defaultdict(deque)
        self._calls: dict[str, int] = defaultdict(int)
        # When True the cloud folds an accepted instruction into the stored status, which is
        # what a healthy appliance reports a second or two later.
        self.apply_instructions = False
        # Appliances the cloud is willing to hand over: serial -> (applianceCode, code).
        self.claimable: dict[str, tuple[str, str]] = {}
        # Serials the cloud knows but will not hand over (not in setup mode).
        self.known_serials: set[str] = set()
        self.bound: list[tuple[str, str]] = []
        # Account-level mutations the tests assert on.
        self.renamed: list[tuple[str, str]] = []
        self.unbound: list[str] = []
        # Appliances the account refuses to unbind, keyed by appliance id.
        self.unbind_refuses: set[str] = set()

    # -- scripting -----------------------------------------------------------------------
    def fail_next(self, kind: str, outcome: Any, times: int = 1) -> None:
        """Queue scripted outcomes for ``kind``: an Exception, httpx.Response or raw dict."""
        for _ in range(times):
            self._scripted[kind].append(outcome)

    def calls(self, kind: str) -> int:
        return self._calls[kind]

    def rotate_token(self, new_token: str = "TOKEN-2") -> None:
        """Expire the token the client currently holds, server-side."""
        self.token = new_token

    # -- inventory helpers ---------------------------------------------------------------
    def add_dishwasher(
        self,
        thing_code: str = DISHWASHER_CODE,
        name: str = "Dishwasher",
        *,
        model: str = DISHWASHER_MODEL,
        online: int = 1,
        capability: list[Any] | None = None,
        state: dict[str, Any] | None = None,
    ) -> None:
        self.devices.append(device_entry(thing_code, name, "0xE1", model, online=online))
        self.capabilities[model] = (
            list(DISHWASHER_CAPABILITY) if capability is None else capability
        )
        self.states[thing_code] = dict(DISHWASHER_STATE if state is None else state)

    def add_lamp(self, thing_code: str = LAMP_CODE, name: str = "Ceiling Lamp") -> None:
        self.devices.append(device_entry(thing_code, name, "0x13", LAMP_MODEL))
        self.capabilities[LAMP_MODEL] = []
        self.states[thing_code] = dict(LAMP_STATE)

    def add_boiler(self, thing_code: str = BOILER_CODE, name: str = "Boiler") -> None:
        self.devices.append(device_entry(thing_code, name, "0xE2", BOILER_MODEL))
        self.capabilities[BOILER_MODEL] = []
        self.states[thing_code] = dict(BOILER_STATE)

    def add_air_conditioner(self, thing_code: str = AC_CODE, name: str = "AC") -> None:
        self.devices.append(device_entry(thing_code, name, "0xAC", AC_MODEL))
        self.capabilities[AC_MODEL] = []
        self.states[thing_code] = dict(AC_STATE)

    def set_attr(self, thing_code: str, **attrs: Any) -> None:
        self.states.setdefault(thing_code, {}).update(attrs)

    def drop_attrs(self, thing_code: str, *keys: str) -> None:
        """Stop reporting ``keys`` — how a partial push frame looks to the integration."""
        state = self.states.setdefault(thing_code, {})
        for key in keys:
            state.pop(key, None)

    # -- binding -------------------------------------------------------------------------
    def _decrypt_serial(self, field: str) -> str:
        """Undo the session encryption the client applies to a serial."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        raw = bytes.fromhex(field)
        key = hashlib.sha256(self.token.encode()).digest()[:16]
        decryptor = Cipher(algorithms.AES(key), modes.CBC(raw[:16])).decryptor()
        padded = decryptor.update(raw[16:]) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode()

    def _verification(self, payload: Any, request: httpx.Request) -> httpx.Response:
        try:
            serial = self._decrypt_serial(payload.get("sn", ""))
        except Exception:
            return httpx.Response(200, json={"code": 9201, "msg": "system error"})
        if serial in self.claimable:
            appliance_code, verification_code = self.claimable[serial]
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "applianceList": [
                            {
                                "applianceCode": appliance_code,
                                "verificationCode": verification_code,
                            }
                        ]
                    },
                },
            )
        if serial in self.known_serials:
            return httpx.Response(200, json={"code": 1210, "msg": "not claimable"})
        return httpx.Response(200, json={"code": 1201, "msg": "unknown serial"})

    def _rename(self, payload: Any, request: httpx.Request) -> httpx.Response:
        thing_code = payload.get("thingCode", "")
        name = payload.get("thingName", "")
        self.renamed.append((thing_code, name))
        for device in self.devices:
            if device["thingCode"] == thing_code:
                device["thingName"] = name
        return httpx.Response(200, json={"code": 0, "msg": "", "data": None})

    def _unbind(self, payload: Any, request: httpx.Request) -> httpx.Response:
        """Remove the appliances from the account, unless scripted to refuse.

        The cloud answers with the list it actually removed, which is not necessarily the
        list it was asked to remove.
        """
        asked = list(payload.get("thingCodes") or [])
        removed = [code for code in asked if code not in self.unbind_refuses]
        self.unbound.extend(removed)
        self.devices = [d for d in self.devices if d["thingCode"] not in removed]
        for code in removed:
            self.states.pop(code, None)
        return httpx.Response(200, json={"code": 0, "msg": "", "data": removed})

    def _bind(self, payload: Any, request: httpx.Request) -> httpx.Response:
        self.bound.append((payload.get("applianceCode"), payload.get("applianceType")))
        return httpx.Response(200, json={"code": 0, "msg": "", "data": None})

    # -- transport -----------------------------------------------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}

        if path.endswith("/user/login/new"):
            return self._dispatch(self.LOGIN, path, payload, self._login, request, auth=False)
        if path.endswith("/user/home/index"):
            return self._dispatch(self.DEVICES, path, payload, self._device_list, request)
        if path.endswith("/function/dict/get"):
            return self._dispatch(self.CAPABILITY, path, payload, self._capability, request)
        if path.endswith("/create/app/cert"):
            return self._dispatch(self.CERTIFICATE, path, payload, self._cert, request)
        if "/appliance/query/" in path:
            return self._dispatch(self.QUERY, path, payload, self._query, request)
        if "/device/command/" in path:
            return self._dispatch(self.COMMAND, path, payload, self._command, request)
        if path.endswith("/appliance/verification"):
            return self._dispatch(
                self.VERIFICATION, path, payload, self._verification, request
            )
        if path.endswith("/business/v1/bind"):
            return self._dispatch(self.BIND, path, payload, self._bind, request)
        if path.endswith("/appliance/name/update"):
            return self._dispatch(self.RENAME, path, payload, self._rename, request)
        if path.endswith("/appliance/binder/remove"):
            return self._dispatch(self.UNBIND, path, payload, self._unbind, request)
        return httpx.Response(404, json={"code": 404, "msg": f"no route for {path}"})

    def _dispatch(
        self,
        kind: str,
        path: str,
        payload: Any,
        handler: Callable[[Any, httpx.Request], httpx.Response],
        request: httpx.Request,
        *,
        auth: bool = True,
    ) -> httpx.Response:
        self._calls[kind] += 1
        self.requests.append((kind, path, payload))
        self.hosts.append(request.url.host)
        if self._scripted[kind]:
            outcome = self._scripted[kind].popleft()
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, httpx.Response):
                return outcome
            if isinstance(outcome, dict):
                return httpx.Response(200, json=outcome)
        if auth:
            rejected = self._check_token(request)
            if rejected is not None:
                return rejected
        return handler(payload, request)

    def _check_token(self, request: httpx.Request) -> httpx.Response | None:
        """Reject a stale token the way the cloud does: business code inside an HTTP 200."""
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header[7:]
        else:
            token = request.headers.get("accessToken", "")
        if token != self.token:
            return httpx.Response(200, json={"code": 14005, "msg": "token invalid"})
        return None

    # -- endpoint handlers ---------------------------------------------------------------
    def _login(self, payload: Any, request: httpx.Request) -> httpx.Response:
        """Validate credentials the way the cloud does — against the encrypted password.

        The expected ciphertext is recomputed here, so a regression in password encryption
        shows up as a failed login rather than as a silently accepted request.
        """
        self.logins += 1
        expected = encrypt_password(ENCRYPT_KEY, self.password)
        if payload.get("loginAccount") != self.account or payload.get("password") != expected:
            return httpx.Response(200, json={"code": 3114016, "msg": "wrong credentials"})
        return httpx.Response(
            200, json={"code": 0, "data": {"accessToken": self.token, "uid": "42"}}
        )

    def _device_list(self, payload: Any, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 0, "msg": "success!", "data": self.devices})

    def _capability(self, payload: Any, request: httpx.Request) -> httpx.Response:
        model = (payload or {}).get("model", "")
        if model not in self.capabilities:
            return httpx.Response(200, json={"code": 3110, "msg": f"unknown model {model}"})
        # The capability list arrives JSON-encoded inside the JSON envelope.
        return httpx.Response(200, json={"code": 0, "data": json.dumps(self.capabilities[model])})

    def _cert(self, payload: Any, request: httpx.Request) -> httpx.Response:
        if self.certificate is None:
            return httpx.Response(200, json={"code": 5001, "msg": "cert service down"})
        return httpx.Response(200, json={"code": 0, "data": self.certificate})

    def _query(self, payload: Any, request: httpx.Request) -> httpx.Response:
        thing_code = request.url.path.rsplit("/", 1)[-1]
        state = self.states.get(thing_code)
        if state is None:
            return httpx.Response(200, json={"code": 3120, "msg": "no such device"})
        return httpx.Response(200, json={"code": 0, "data": dict(state)})

    def _command(self, payload: Any, request: httpx.Request) -> httpx.Response:
        thing_code = request.url.path.rsplit("/", 1)[-1]
        instruction = (payload or {}).get("instruction") or {}
        self.instructions.append((thing_code, dict(instruction)))
        if self.apply_instructions:
            self.states.setdefault(thing_code, {}).update(instruction)
        return httpx.Response(200, json={"code": 0, "msg": "success!"})


# --- fake push broker --------------------------------------------------------------------


class FakeMqttBroker:
    """Replacement for ``MqttClient`` that records subscriptions and injects frames."""

    def __init__(self, **kwargs: Any) -> None:
        self.endpoint: str = kwargs["endpoint"]
        self.port: int = kwargs["port"]
        self.client_id: str = kwargs["client_id"]
        self.ssl_context = kwargs["ssl_context"]
        self._on_message = kwargs["on_message"]
        self.subscriptions: list[str] = []
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.connect_error: Exception | None = None
        self.on_subscribe: Callable[[FakeMqttBroker, str], None] | None = None

    def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    def subscribe(self, topics: str | Iterable[str]) -> None:
        if isinstance(topics, str):
            topics = [topics]
        for topic in topics:
            self.subscriptions.append(topic)
            if self.on_subscribe is not None:
                self.on_subscribe(self, topic)

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False

    def deliver(self, topic: str, payload: dict[str, Any]) -> None:
        """Hand a frame to the coordinator the way paho does — off the event loop thread."""
        self._on_message(topic, payload)


class MqttSpy:
    """Collects every broker the coordinator builds, and can sabotage the next one."""

    def __init__(self) -> None:
        self.instances: list[FakeMqttBroker] = []
        self.connect_error: Exception | None = None
        self.construct_error: Exception | None = None
        self.on_subscribe: Callable[[FakeMqttBroker, str], None] | None = None

    def __call__(self, **kwargs: Any) -> FakeMqttBroker:
        if self.construct_error is not None:
            raise self.construct_error
        broker = FakeMqttBroker(**kwargs)
        broker.connect_error = self.connect_error
        broker.on_subscribe = self.on_subscribe
        self.instances.append(broker)
        return broker

    @property
    def last(self) -> FakeMqttBroker:
        assert self.instances, "no push connection was established"
        return self.instances[-1]


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of the custom integration in every test."""
    yield


@pytest.fixture
def cloud() -> FakeCloud:
    """An account holding a single dishwasher; tests add further devices as needed."""
    fake = FakeCloud()
    fake.add_dishwasher()
    return fake


@pytest.fixture
def mqtt_spy() -> MqttSpy:
    return MqttSpy()


@pytest.fixture
def config_entry(hass: HomeAssistant, cloud: FakeCloud) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=cloud.account,
        unique_id=cloud.account,
        data={
            CONF_ACCOUNT: cloud.account,
            "password": cloud.password,
            CONF_REGION: "eu",
            "country": "RU",
            "device_id": "hbtestdevice0000000000000000",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def patched_cloud(cloud: FakeCloud, mqtt_spy: MqttSpy):
    """Route the integration's HTTP at the fake cloud and stub push + TLS.

    Only what must not happen in a test is replaced: real sockets (``MockTransport``), the
    mutual-TLS push connection, and writing certificate material to the config directory.
    Everything between the HA platform and the wire is production code.
    """

    class ClientFactory:
        """Drop-in for ``DollinClient``, exposing only the ``create`` entry point used."""

        @staticmethod
        def create(http_client: httpx.AsyncClient, store: Any, **kwargs: Any) -> DollinClient:
            # Keep the caller's own client so a test can assert it is closed again.
            cloud.owned_clients.append(http_client)
            mocked = httpx.AsyncClient(transport=httpx.MockTransport(cloud.handle))
            return DollinClient.create(mocked, store, **kwargs)

    with (
        patch("custom_components.holabrain.DollinClient", ClientFactory),
        patch("custom_components.holabrain.config_flow.DollinClient", ClientFactory),
        patch("custom_components.holabrain.coordinator.MqttClient", mqtt_spy),
        patch(
            "custom_components.holabrain.coordinator.build_client_ssl_context",
            return_value=MagicMock(name="ssl_context"),
        ),
        patch.object(
            HolabrainCoordinator,
            "_write_cert_files",
            lambda self, key_pem, cert_pem: ("cert.pem", "key.pem"),
        ),
    ):
        yield cloud


@pytest.fixture
def setup_integration(hass: HomeAssistant, config_entry: MockConfigEntry, patched_cloud):
    """Return an awaitable that sets the config entry up and settles the event loop."""

    async def _setup() -> bool:
        result = await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        return result

    return _setup


@pytest.fixture
def make_coordinator(
    hass: HomeAssistant, config_entry: MockConfigEntry, patched_cloud: FakeCloud
):
    """Build a coordinator directly, without going through the config-entry lifecycle.

    Needed for the races that only exist *before* the first refresh — by the time an entry
    is loaded, that window has already closed.
    """

    def _make() -> HolabrainCoordinator:
        http = httpx.AsyncClient(transport=httpx.MockTransport(patched_cloud.handle))
        client = DollinClient.create(
            http,
            InMemoryTokenStore(),
            region="eu",
            account=patched_cloud.account,
            password=patched_cloud.password,
            country="RU",
            device_id="hbtestdevice0000000000000000",
        )
        return HolabrainCoordinator(hass, config_entry, client)

    return _make


@pytest.fixture
def push(hass: HomeAssistant, mqtt_spy: MqttSpy):
    """Deliver a push frame from a worker thread, then settle the event loop."""

    async def _push(
        topic: str, payload: dict[str, Any], broker: FakeMqttBroker | None = None
    ) -> None:
        target = broker or mqtt_spy.last
        await hass.async_add_executor_job(target.deliver, topic, payload)
        await hass.async_block_till_done()

    return _push


@pytest.fixture
def entity_id_of(hass: HomeAssistant):
    """Resolve an entity id from its unique id (``<thing_code>_<key>``).

    Entity ids derive from translated names and are brittle; the unique id is the
    integration's actual contract with the entity registry.
    """

    def _lookup(domain: str, unique_id: str) -> str | None:
        return er.async_get(hass).async_get_entity_id(domain, DOMAIN, unique_id)

    return _lookup
