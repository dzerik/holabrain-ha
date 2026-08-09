"""AuthManager tests: login persistence and the re-login-on-auth-failure retry.

Uses a fake transport (duck-typed) so the auth logic is tested without any HTTP stack.
"""

import pytest

from custom_components.holabrain.aiodollin.auth.manager import AuthManager
from custom_components.holabrain.aiodollin.auth.store import InMemoryTokenStore, Session
from custom_components.holabrain.aiodollin.exceptions import (
    AuthError,
    CredentialsRejectedError,
    TokenExpiredError,
)


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


@pytest.mark.asyncio
async def test_an_expired_token_is_replaced_and_the_request_retried():
    """The natural end of a token's life must be invisible to the caller."""
    transport = FakeTransport(
        oem=[
            TokenExpiredError("token invalid"),
            {"code": 0, "data": {"accessToken": "TOK-2", "uid": "42"}},
            {"code": 0, "data": {"ok": 1}},
        ]
    )
    auth = AuthManager(
        transport, InMemoryTokenStore(Session(access_token="STALE")), account="a@b.c",
        password="pw",
    )

    result = await auth.oem("/v1/thing")

    assert result["data"]["ok"] == 1
    # The retried request must carry the new token, not the one the cloud just refused.
    assert transport.oem_calls[-1][2] == "TOK-2"


@pytest.mark.asyncio
async def test_an_expiry_is_not_counted_as_a_session_takeover():
    """The back-off exists to stop a tug-of-war with the phone app. A token reaching the
    end of its life is not a tug-of-war, and charging it there escalates the cool-down
    until a later poll is refused outright."""
    transport = FakeTransport(
        oem=[
            TokenExpiredError("token invalid"),
            {"code": 0, "data": {"accessToken": "TOK-2", "uid": "42"}},
            {"code": 0, "data": {"ok": 1}},
        ]
    )
    auth = AuthManager(
        transport, InMemoryTokenStore(Session(access_token="STALE")), account="a@b.c",
        password="pw",
    )

    await auth.oem("/v1/thing")

    assert auth.evictions == 0


@pytest.mark.asyncio
async def test_rejected_credentials_never_trigger_another_login():
    """Resending a password the cloud just refused is how an account gets locked."""
    transport = FakeTransport(oem=[CredentialsRejectedError("wrong credentials")])
    auth = AuthManager(
        transport, InMemoryTokenStore(Session(access_token="STALE")), account="a@b.c",
        password="pw",
    )

    with pytest.raises(CredentialsRejectedError):
        await auth.oem("/v1/thing")

    # Exactly one request: the original. No login was attempted.
    assert len(transport.oem_calls) == 1


@pytest.mark.asyncio
async def test_a_second_expiry_after_the_retry_is_not_retried_again():
    """One retry, never a loop — a cloud that rejects every token must not be hammered."""
    transport = FakeTransport(
        oem=[
            TokenExpiredError("token invalid"),
            {"code": 0, "data": {"accessToken": "TOK-2", "uid": "42"}},
            TokenExpiredError("token invalid"),
        ]
    )
    auth = AuthManager(
        transport, InMemoryTokenStore(Session(access_token="STALE")), account="a@b.c",
        password="pw",
    )

    with pytest.raises(TokenExpiredError):
        await auth.oem("/v1/thing")

    assert len(transport.oem_calls) == 3  # original, login, retry — and no more


@pytest.mark.asyncio
async def test_a_failed_relogin_does_not_leave_a_dead_token_behind():
    """A stored token the cloud has already refused must never be handed out as usable."""
    store = InMemoryTokenStore(Session(access_token="STALE"))
    transport = FakeTransport(
        oem=[TokenExpiredError("token invalid"), CredentialsRejectedError("wrong password")]
    )
    auth = AuthManager(transport, store, account="a@b.c", password="pw")

    with pytest.raises(CredentialsRejectedError):
        await auth.oem("/v1/thing")

    assert await store.load() is None
