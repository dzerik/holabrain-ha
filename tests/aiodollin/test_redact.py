"""Identifiers must not reach a log file that ends up attached to a public issue.

Debug logging exists to make a cloud failure reportable, which means the reports get
pasted into GitHub issues. An appliance id in a URL is the one identifier the request
path carries by construction, so it has to be dealt with at the point of formatting
rather than by asking people to scrub their own logs.
"""

import logging

import httpx
import pytest

from custom_components.holabrain.aiodollin.exceptions import ApiError
from custom_components.holabrain.aiodollin.redact import pseudonym, redact_path
from custom_components.holabrain.aiodollin.transport.http import HttpTransport

THING_CODE = "100000000000001"


def test_a_pseudonym_hides_the_id_but_stays_the_same_across_reports():
    """Two reports from the same user must be comparable, so the mapping is stable.

    "It broke again after the update" is only useful if the same appliance is recognisable
    in both attachments — but the real id must not be recoverable from either.
    """
    first = pseudonym(THING_CODE)
    second = pseudonym(THING_CODE)

    assert first == second
    assert THING_CODE not in first
    assert pseudonym("200000000000002") != first


def test_an_appliance_id_in_a_path_is_replaced():
    """The device endpoints put the id in the URL, so the path alone leaks it."""
    path = f"/v1/appliance/deviceCommands/query/{THING_CODE}"

    safe = redact_path(path)

    assert THING_CODE not in safe
    assert safe.startswith("/v1/appliance/deviceCommands/query/")
    assert pseudonym(THING_CODE) in safe


def test_a_path_without_an_id_is_untouched():
    """Most paths carry no identifier; mangling them would only make logs harder to read."""
    assert redact_path("/v1/homeIndex") == "/v1/homeIndex"
    assert redact_path("/midea/open/business/v1/appliance/query") == (
        "/midea/open/business/v1/appliance/query"
    )


def test_a_short_number_in_a_path_is_not_mistaken_for_an_id():
    """Version segments and small numbers are part of the endpoint, not identity."""
    assert redact_path("/v1/oemTimer/e2/12345") == "/v1/oemTimer/e2/12345"


@pytest.mark.asyncio
async def test_debug_logging_a_device_call_leaks_neither_the_id_nor_the_token(caplog):
    """Turning debug on must not turn a log file into something unsafe to attach.

    The path carries the appliance id and the headers carry a live access token, and both
    pass through the one place that formats these messages.
    """
    token = "TOKEN-THAT-MUST-NOT-APPEAR"

    def handler(request):
        return httpx.Response(200, json={"code": 0, "data": {}})

    caplog.set_level(logging.DEBUG, logger="custom_components.holabrain.aiodollin")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpTransport(client, region="eu")
    await transport.oem_request(
        f"/v1/appliance/deviceCommands/query/{THING_CODE}", {"query": "1"}, access_token=token
    )
    await client.aclose()

    # Only this package's own records: httpx logs the full URL, appliance id and all, at
    # INFO from its own logger, which is why docs/troubleshooting.md tells people to quiet
    # it before attaching a log. What is asserted here is what this code emits.
    ours = "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("custom_components.holabrain")
    )
    assert ours, "the request should have been logged at debug"
    assert THING_CODE not in ours
    assert token not in ours
    assert pseudonym(THING_CODE) in ours


@pytest.mark.asyncio
async def test_an_unmapped_business_code_is_logged_with_its_number(caplog):
    """The number is the whole point: the cloud's text names the symptom, never the code."""

    def handler(request):
        return httpx.Response(200, json={"code": 40004, "msg": "Token has expired"})

    caplog.set_level(logging.DEBUG, logger="custom_components.holabrain.aiodollin")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = HttpTransport(client, region="eu")
    with pytest.raises(ApiError):
        await transport.oem_request("/v1/homeIndex")
    await client.aclose()

    assert "40004" in caplog.text
    assert "Token has expired" in caplog.text
