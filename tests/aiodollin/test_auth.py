"""AuthManager tests: login persistence and the re-login-on-auth-failure retry.

Uses a fake transport (duck-typed) so the auth logic is tested without any HTTP stack.
"""

import pytest

from custom_components.holabrain.aiodollin.auth.manager import AuthManager
from custom_components.holabrain.aiodollin.auth.store import InMemoryTokenStore, Session
from custom_components.holabrain.aiodollin.exceptions import AuthError


class FakeTransport:
    """Serves queued oem/tob responses; raises queued exceptions."""

    region = "eu"

    def __init__(self, oem=None, tob=None):
        self._oem = list(oem or [])
        self._tob = list(tob or [])
        self.oem_calls: list[tuple] = []
        self.tob_calls: list[tuple] = []

    async def oem_request(self, path, payload=None, *, access_token=""):
        self.oem_calls.append((path, payload, access_token))
        return _pop(self._oem)

    async def tob_request(self, path, payload=None, *, access_token=""):
        self.tob_calls.append((path, payload, access_token))
        return _pop(self._tob)


def _pop(queue):
    item = queue.pop(0)
    if isinstance(item, Exception):
        raise item
    return item


@pytest.mark.asyncio
async def test_login_persists_session_and_sends_encrypted_password():
    transport = FakeTransport(oem=[{"code": 0, "data": {"accessToken": "TOK", "uid": "42"}}])
    store = InMemoryTokenStore()
    auth = AuthManager(transport, store, account="a@b.c", password="pw", country="RU")

    token = await auth.async_login()

    assert token == "TOK"
    saved = await store.load()
    assert saved and saved.access_token == "TOK" and saved.uid == "42"
    # Password must be encrypted, never sent in the clear.
    _, body, _ = transport.oem_calls[0]
    assert body["password"] != "pw"
    assert body["loginAccount"] == "a@b.c"


@pytest.mark.asyncio
async def test_get_token_uses_stored_session_without_login():
    transport = FakeTransport()  # no responses queued → a login attempt would IndexError
    store = InMemoryTokenStore(Session(access_token="CACHED"))
    auth = AuthManager(transport, store, account="a", password="p")

    assert await auth.async_get_token() == "CACHED"
    assert transport.oem_calls == []  # never hit the network


@pytest.mark.asyncio
async def test_oem_retries_once_after_auth_error():
    # First business call fails auth → manager re-logs-in → retries and succeeds.
    transport = FakeTransport(
        oem=[
            AuthError("expired"),
            {"code": 0, "data": {"accessToken": "NEW"}},
            {"code": 0, "data": {"ok": 1}},
        ]
    )
    store = InMemoryTokenStore(Session(access_token="OLD"))
    auth = AuthManager(transport, store, account="a", password="p")

    result = await auth.oem("/v1/thing")

    assert result["data"]["ok"] == 1
    # Called with the stale token first, then re-login, then the fresh token.
    assert transport.oem_calls[0][2] == "OLD"
    assert transport.oem_calls[-1][2] == "NEW"


@pytest.mark.asyncio
async def test_login_without_credentials_raises():
    auth = AuthManager(FakeTransport(), InMemoryTokenStore(), account="", password="")
    with pytest.raises(AuthError):
        await auth.async_login()


@pytest.mark.asyncio
async def test_auth_error_without_credentials_is_not_retried_into_a_loop():
    # A stored token that the server rejects, with no credentials to re-login, must surface
    # as AuthError (→ HA reauth) rather than retry forever.
    transport = FakeTransport(tob=[AuthError("nope")])
    store = InMemoryTokenStore(Session(access_token="OLD"))
    auth = AuthManager(transport, store, account="", password="")
    with pytest.raises(AuthError):
        await auth.tob("/midea/open/business/v1/x")
