"""DeviceApi tests against a fake AuthManager, using the real device-list shape."""

import pytest

from custom_components.holabrain.aiodollin.api.devices import DeviceApi
from custom_components.holabrain.aiodollin.exceptions import ApiError

# The exact shape the account device list returns for a dishwasher.
_HOME_INDEX = {
    "code": 0,
    "msg": "success!",
    "data": [
        {
            "thingCode": "100000000000001",
            "thingName": "Посудомоечная Машина Weissgauff1",
            "deviceType": "0xE1",
            "firmwareVersion": "059006092306",
            "model": "760EY179",
            "online": 1,
            "sn8": "760EY179",
            "pluginType": 1,
        }
    ],
}


class FakeAuth:
    def __init__(self, oem=None, tob=None):
        self._oem = list(oem or [])
        self._tob = list(tob or [])
        self.oem_calls: list[tuple] = []
        self.tob_calls: list[tuple] = []

    async def oem(self, path, payload=None):
        self.oem_calls.append((path, payload))
        return self._oem.pop(0)

    async def tob(self, path, payload=None):
        self.tob_calls.append((path, payload))
        return self._tob.pop(0)


@pytest.mark.asyncio
async def test_list_parses_devices():
    api = DeviceApi(FakeAuth(oem=[_HOME_INDEX]))
    devices = await api.async_list()
    assert len(devices) == 1
    dev = devices[0]
    assert dev.thing_code == "100000000000001"
    assert dev.device_type == "0xE1"
    assert dev.model == "760EY179"
    assert dev.online is True
    assert dev.name.startswith("Посудомоечная")


@pytest.mark.asyncio
async def test_list_empty_when_no_devices():
    api = DeviceApi(FakeAuth(oem=[{"code": 0, "data": []}]))
    assert await api.async_list() == []


@pytest.mark.asyncio
async def test_list_tolerates_missing_data_key():
    api = DeviceApi(FakeAuth(oem=[{"code": 0}]))
    assert await api.async_list() == []


@pytest.mark.asyncio
async def test_get_state_reads_status():
    auth = FakeAuth(tob=[{"code": 0, "data": {"power": "3", "runState": "1"}}])
    api = DeviceApi(auth)
    state = await api.async_get_state("100000000000001")
    assert state.get("power") == "3"
    # Query must ask for a full snapshot.
    assert auth.tob_calls[0][1] == {"query": "1"}


@pytest.mark.asyncio
async def test_get_state_without_body_raises():
    api = DeviceApi(FakeAuth(tob=[{"code": 0, "data": None}]))
    with pytest.raises(ApiError):
        await api.async_get_state("t")


@pytest.mark.asyncio
async def test_send_instruction_wraps_payload_and_targets_thing():
    auth = FakeAuth(tob=[{"code": 0}])
    api = DeviceApi(auth)
    await api.async_send_instruction("100000000000001", {"runState": "2"})
    path, payload = auth.tob_calls[0]
    assert "100000000000001" in path
    assert payload == {"instruction": {"runState": "2"}}


# thingProtocol 2 (e.g. an 0xE2 water heater): the vendor's own per-model plugin bundle shows
# these calls signed with the OEM scheme, not ToB, and without the `/midea/open/business`
# prefix — verified against a real account (see PR description for the raw evidence).
_HOME_INDEX_ALT_PROTOCOL = {
    "code": 0,
    "msg": "success!",
    "data": [
        {
            "thingCode": "200000000000002",
            "thingName": "Ewh1",
            "deviceType": "0xE2",
            "firmwareVersion": "1.1.9",
            "model": "51020ED8",
            "online": 1,
            "thingProtocol": 2,
        }
    ],
}


@pytest.mark.asyncio
async def test_get_state_uses_oem_scheme_for_alt_protocol():
    auth = FakeAuth(
        oem=[_HOME_INDEX_ALT_PROTOCOL, {"code": 0, "data": {"power": "1"}}],
    )
    api = DeviceApi(auth)
    await api.async_list()
    state = await api.async_get_state("200000000000002")

    assert state.get("power") == "1"
    assert auth.tob_calls == []
    path, payload = auth.oem_calls[1]
    assert path == "/v1/appliance/deviceCommands/query/200000000000002"
    assert payload == {"query": "1"}


@pytest.mark.asyncio
async def test_send_instruction_uses_oem_scheme_for_alt_protocol():
    auth = FakeAuth(oem=[_HOME_INDEX_ALT_PROTOCOL, {"code": 0}])
    api = DeviceApi(auth)
    await api.async_list()
    await api.async_send_instruction("200000000000002", {"targetTemp": "60"})

    assert auth.tob_calls == []
    path, payload = auth.oem_calls[1]
    assert path == "/v1/appliance/deviceCommands/requestNoReply/200000000000002"
    assert payload == {"instruction": {"targetTemp": "60"}}


@pytest.mark.asyncio
async def test_a_junk_numeric_field_does_not_take_the_whole_account_down():
    """One unparsable field must cost that field, not the device list.

    The same numeric fields come back as ints, as strings, as ``null`` and occasionally as
    an empty string. Letting a ``ValueError`` escape here would fail the inventory read, and
    with it the setup of every appliance on the account.
    """
    api = DeviceApi(
        FakeAuth(
            oem=[
                {
                    "code": 0,
                    "data": [
                        {
                            "thingCode": "100000000000001",
                            "thingName": "Dishwasher",
                            "deviceType": "0xe1",
                            "sn8": "760EY179",
                            "online": 1,
                            "pluginType": "",
                            "thingProtocol": "not a number",
                        }
                    ],
                }
            ]
        )
    )

    devices = await api.async_list()

    assert len(devices) == 1
    assert devices[0].plugin_type == 0
    # Falls back to the dialect verified against hardware rather than to an invalid one.
    assert devices[0].thing_protocol == 1
    # The device type is normalized whatever case the cloud used.
    assert devices[0].device_type == "0xE1"
