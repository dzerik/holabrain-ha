"""Account binding: claiming, releasing and labelling appliances.

The cloud answers "I have never heard of this serial" and "I know it but you cannot have it
right now" with two different business codes. Collapsing them into one error would leave the
user unable to tell a typo from an appliance that is simply not in setup mode — the single
most likely thing to go wrong.
"""

import pytest

from custom_components.holabrain.aiodollin.api.binding import (
    BindingApi,
    NotClaimable,
    SerialUnknown,
)
from custom_components.holabrain.aiodollin.exceptions import ApiError

SERIAL = "0000E1540760EY1790000000000EXAMP"
CODE = "0011223344556677889900aabbccddee"


class FakeAuth:
    def __init__(self, tob=None, oem=None) -> None:
        self._tob = list(tob or [])
        self._oem = list(oem or [])
        self.tob_calls: list[tuple] = []
        self.oem_calls: list[tuple] = []

    async def async_get_token(self) -> str:
        return "TOKEN"

    async def tob(self, path, payload=None):
        self.tob_calls.append((path, payload))
        return _pop(self._tob)

    async def oem(self, path, payload=None):
        self.oem_calls.append((path, payload))
        return _pop(self._oem)


def _pop(queue):
    item = queue.pop(0)
    if isinstance(item, Exception):
        raise item
    return item


@pytest.mark.asyncio
async def test_finding_an_appliance_returns_what_can_be_claimed():
    auth = FakeAuth(
        tob=[
            {
                "code": 0,
                "data": {
                    "applianceList": [
                        {"applianceCode": "1539316", "verificationCode": CODE}
                    ]
                },
            }
        ]
    )
    api = BindingApi(auth)

    found = await api.async_find(SERIAL, CODE)

    assert len(found) == 1
    assert found[0].appliance_code == "1539316"
    assert found[0].verification_code == CODE


@pytest.mark.asyncio
async def test_the_serial_never_leaves_the_process_in_the_clear():
    """A plain serial is refused by the cloud, and it identifies the appliance."""
    auth = FakeAuth(tob=[{"code": 0, "data": {"applianceList": []}}])
    api = BindingApi(auth)

    await api.async_find(SERIAL, CODE)

    _, payload = auth.tob_calls[0]
    assert SERIAL not in payload["sn"]
    assert payload["randomCode"] == CODE


@pytest.mark.asyncio
async def test_an_unknown_serial_is_distinguishable_from_a_busy_appliance():
    """A typo and "press the button on the appliance" need different advice."""
    api = BindingApi(FakeAuth(tob=[ApiError("unknown", code=1201)]))
    with pytest.raises(SerialUnknown):
        await api.async_find(SERIAL, CODE)

    api = BindingApi(FakeAuth(tob=[ApiError("not offering", code=1210)]))
    with pytest.raises(NotClaimable):
        await api.async_find(SERIAL, CODE)


@pytest.mark.asyncio
async def test_an_unrelated_failure_is_not_reinterpreted():
    """Only the two documented codes get a friendly meaning; the rest stay as they are."""
    api = BindingApi(FakeAuth(tob=[ApiError("rate limited", code=4001)]))

    with pytest.raises(ApiError) as excinfo:
        await api.async_find(SERIAL, CODE)

    assert not isinstance(excinfo.value, (SerialUnknown, NotClaimable))


@pytest.mark.asyncio
async def test_an_empty_result_is_not_an_error():
    """The cloud can answer successfully with nothing to claim; that is a normal outcome."""
    api = BindingApi(FakeAuth(tob=[{"code": 0, "data": {}}]))

    assert await api.async_find(SERIAL, CODE) == []


@pytest.mark.asyncio
async def test_binding_sends_every_field_the_cloud_requires():
    auth = FakeAuth(tob=[{"code": 0}])
    api = BindingApi(auth)

    await api.async_bind("1539316", CODE, "0xE1", time_zone_id="Europe/Moscow")

    _, payload = auth.tob_calls[0]
    assert payload == {
        "timeZoneID": "Europe/Moscow",
        "applianceType": "0xE1",
        "applianceCode": "1539316",
        "verificationCode": CODE,
    }


@pytest.mark.asyncio
async def test_unbinding_reports_what_the_cloud_actually_removed():
    """Asking for two and getting one back is a partial failure the caller must see."""
    auth = FakeAuth(oem=[{"code": 0, "data": ["a"]}])
    api = BindingApi(auth)

    removed = await api.async_unbind(["a", "b"])

    assert removed == ["a"]
    assert auth.oem_calls[0][1] == {"thingCodes": ["a", "b"]}


@pytest.mark.asyncio
async def test_renaming_and_location_use_the_appliance_id_the_account_knows():
    auth = FakeAuth(oem=[{"code": 0}, {"code": 0}])
    api = BindingApi(auth)

    await api.async_rename("t1", "Kitchen dishwasher")
    await api.async_set_location("t1", "Kitchen")

    assert auth.oem_calls[0][1] == {"thingCode": "t1", "thingName": "Kitchen dishwasher"}
    assert auth.oem_calls[1][1]["location"] == "Kitchen"
    assert auth.oem_calls[1][1]["thingCode"] == "t1"


@pytest.mark.asyncio
async def test_a_non_ascii_name_is_passed_through_unchanged():
    """Appliance names are user text; mangling them here would be silent data loss."""
    auth = FakeAuth(oem=[{"code": 0}])
    api = BindingApi(auth)

    await api.async_rename("t1", "Посудомоечная машина")

    assert auth.oem_calls[0][1]["thingName"] == "Посудомоечная машина"


@pytest.mark.asyncio
async def test_auth_status_survives_a_response_without_it():
    """A missing status must read as unknown, not as zero — zero means something."""
    missing = BindingApi(FakeAuth(tob=[{"code": 0, "data": {}}]))
    assert await missing.async_auth_status("a") is None
    absent = BindingApi(FakeAuth(tob=[{"code": 0, "data": None}]))
    assert await absent.async_auth_status("a") is None
    api = BindingApi(FakeAuth(tob=[{"code": 0, "data": {"status": 0}}]))
    assert await api.async_auth_status("a") == 0
