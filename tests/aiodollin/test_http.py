"""HTTP transport tests using httpx's in-memory MockTransport (no live network).

These cover the parts that fail silently in production if wrong: header/signature framing,
exact request-body bytes (including non-ASCII), and the mapping of cloud business codes to
the typed exception hierarchy.
"""

import httpx
import pytest

from custom_components.holabrain.aiodollin.auth.signer import tob_sign
from custom_components.holabrain.aiodollin.const import CLIENT_SECRET
from custom_components.holabrain.aiodollin.exceptions import (
    ApiError,
    AuthError,
    NetworkError,
    RateLimitError,
)
from custom_components.holabrain.aiodollin.transport.http import HttpTransport


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_oem_request_sets_signature_headers_and_exact_body():
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        seen["content"] = request.content
        return httpx.Response(200, json={"code": 0, "data": {"ok": 1}})

    client = _client(handler)
    transport = HttpTransport(client, region="eu", device_id="hbtest")
    data = await transport.oem_request("/v1/x", {"a": "b"})

    assert data["data"]["ok"] == 1
    assert seen["headers"]["appId"] == "1020"
    assert seen["headers"]["sign"]  # OEM signature present
    assert seen["headers"]["deviceId"] == "hbtest"
    # Body must be compact JSON bytes exactly as signed.
    assert seen["content"] == b'{"a":"b"}'
    await client.aclose()


@pytest.mark.asyncio
async def test_tob_request_signature_matches_body_on_the_wire():
    seen = {}
    path = "/midea/open/business/v1/device/command/T"

    def handler(request):
        seen["sig"] = request.headers["Signature"]
        seen["content"] = request.content
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"code": 0})

    client = _client(handler)
    transport = HttpTransport(client, region="eu")
    await transport.tob_request(path, {"instruction": {"runState": "1"}}, access_token="TOK")

    body = seen["content"].decode("utf-8")
    assert seen["sig"] == tob_sign(CLIENT_SECRET, "POST", path, body)
    assert seen["auth"] == "Bearer TOK"
    await client.aclose()


@pytest.mark.asyncio
async def test_non_ascii_body_is_utf8_not_escaped():
    seen = {}
    path = "/midea/open/business/v1/device/command/T"

    def handler(request):
        seen["content"] = request.content
        seen["sig"] = request.headers["Signature"]
        return httpx.Response(200, json={"code": 0})

    client = _client(handler)
    transport = HttpTransport(client, region="eu")
    await transport.tob_request(path, {"instruction": {"name": "Пуск"}}, access_token="T")

    assert "Пуск" in seen["content"].decode("utf-8")
    assert b"\\u041f" not in seen["content"]  # not ASCII-escaped
    assert seen["sig"] == tob_sign(CLIENT_SECRET, "POST", path, seen["content"].decode())
    await client.aclose()


@pytest.mark.parametrize(
    ("code", "expected"),
    [(14005, AuthError), (3114016, AuthError), (5, AuthError), (9999, ApiError)],
)
@pytest.mark.asyncio
async def test_business_error_codes_map_to_exceptions(code, expected):
    client = _client(lambda r: httpx.Response(200, json={"code": code, "msg": "x"}))
    transport = HttpTransport(client, region="eu")
    with pytest.raises(expected):
        await transport.oem_request("/v1/x")
    await client.aclose()


@pytest.mark.asyncio
async def test_api_error_carries_code():
    client = _client(lambda r: httpx.Response(200, json={"code": 9999, "msg": "boom"}))
    transport = HttpTransport(client, region="eu")
    with pytest.raises(ApiError) as excinfo:
        await transport.oem_request("/v1/x")
    assert excinfo.value.code == 9999
    await client.aclose()


@pytest.mark.asyncio
async def test_http_429_is_rate_limit():
    client = _client(lambda r: httpx.Response(429, text="slow down"))
    transport = HttpTransport(client, region="eu")
    with pytest.raises(RateLimitError):
        await transport.oem_request("/v1/x")
    await client.aclose()


@pytest.mark.asyncio
async def test_non_json_response_is_api_error():
    client = _client(lambda r: httpx.Response(200, text="<html>gateway</html>"))
    transport = HttpTransport(client, region="eu")
    with pytest.raises(ApiError):
        await transport.oem_request("/v1/x")
    await client.aclose()


@pytest.mark.asyncio
async def test_transport_failure_is_network_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    client = _client(handler)
    transport = HttpTransport(client, region="eu")
    with pytest.raises(NetworkError):
        await transport.oem_request("/v1/x")
    await client.aclose()


def test_unknown_region_rejected():
    with pytest.raises(ValueError):
        HttpTransport(httpx.AsyncClient(), region="mars")
