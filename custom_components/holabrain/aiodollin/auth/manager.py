"""Authentication manager: login, token caching, and authenticated request retry.

All authenticated API calls go through :meth:`AuthManager.oem` / :meth:`AuthManager.tob`.
Those attach the current token and, on an :class:`AuthError`, log in again once and retry.

Single session per account
--------------------------
The cloud keeps **one** live session per account: a new login immediately invalidates the
previous token, which then fails every request. Home Assistant therefore shares one session
slot with the vendor's mobile app — whoever logged in last owns it.

Two consequences shape this class:

* **Never log in when a stored token would do.** The session is loaded from the token store
  first, so a restart does not evict the app.
* **Never log in in a tight loop.** When the session is taken away, an immediate re-login
  steals it straight back, the app re-logs in, and the two sides ping-pong forever — hammering
  the account and breaking both clients. Re-login is therefore rate limited with a growing
  cool-down, so a user actively using the app is left alone and Home Assistant recovers on
  its own once the app goes idle.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from ..const import ENCRYPT_KEY, EP_LOGIN
from ..exceptions import AuthError, SessionTakeoverError
from ..transport.http import HttpTransport
from .signer import encrypt_password
from .store import Session, TokenStore

# Cool-down before reclaiming a session that was taken over, indexed by how many takeovers
# happened recently. The first one retries immediately — it is usually just an expired token.
# Further ones back off, because a takeover shortly after we reclaimed the session means
# another client is actively using the account and racing it helps nobody.
LOGIN_BACKOFF_SECONDS: tuple[float, ...] = (0.0, 60.0, 300.0, 900.0)

# A takeover this long after the previous one is unrelated, so the counter starts over.
EVICTION_FORGET_SECONDS = 1800.0


class AuthManager:
    """Owns the account session and produces authenticated requests."""

    def __init__(
        self,
        transport: HttpTransport,
        store: TokenStore,
        *,
        account: str = "",
        password: str = "",
        country: str = "RU",
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._transport = transport
        self._store = store
        self._account = account
        self._password = password
        self._country = country
        self._token: str | None = None
        self._clock = clock or time.monotonic
        self._last_login: float | None = None
        self._last_eviction: float | None = None
        self._evictions = 0

    @property
    def evictions(self) -> int:
        """How many times in a row the session was taken over by another client."""
        return self._evictions

    def _cooldown(self) -> float:
        index = min(self._evictions, len(LOGIN_BACKOFF_SECONDS) - 1)
        return LOGIN_BACKOFF_SECONDS[index]

    def _note_eviction(self) -> float:
        """Record a takeover and return how long to wait before reclaiming the session."""
        now = self._clock()
        if (
            self._last_eviction is not None
            and now - self._last_eviction > EVICTION_FORGET_SECONDS
        ):
            self._evictions = 0
        self._last_eviction = now
        cooldown = self._cooldown()
        self._evictions += 1
        return cooldown

    async def async_get_token(self) -> str:
        """Return a usable access token, logging in only if none is cached/stored."""
        if self._token:
            return self._token
        session = await self._store.load()
        if session and session.access_token:
            self._token = session.access_token
            return self._token
        return await self.async_login()

    async def async_login(self) -> str:
        """Perform a fresh login and persist the session.

        Unconditional: the cool-down belongs to :meth:`_async_recover`, which is the only
        path that can loop. An explicit login (setup, re-authentication) must always run.
        """
        if not (self._account and self._password):
            raise AuthError("no credentials available to authenticate")

        body = {
            "loginAccount": self._account,
            "password": encrypt_password(ENCRYPT_KEY, self._password),
            "pushToken": "",
            "countryCode": self._country,
            "isRedirectLogin": False,
        }
        data = await self._transport.oem_request(EP_LOGIN, body)
        info = data.get("data") if isinstance(data.get("data"), dict) else {}
        token = (info or {}).get("accessToken")
        if not token:
            raise AuthError("login succeeded but returned no access token")
        self._token = str(token)
        self._last_login = self._clock()
        await self._store.save(
            Session(
                access_token=self._token,
                account=self._account,
                region=self._transport.region,
                uid=str((info or {}).get("uid", "")),
            )
        )
        return self._token

    async def _async_recover(self) -> str:
        """Handle a rejected token: the session was taken over, so reclaim it.

        Reclaiming is delayed while another client keeps taking the session back, otherwise
        the two sides ping-pong: every reclaim logs the other one out, which makes it log in
        again, which logs us out. Waiting lets the other client finish; Home Assistant picks
        the session up on a later cycle.
        """
        cooldown = self._note_eviction()
        self._token = None
        await self._store.clear()
        if cooldown and self._last_login is not None:
            waited = self._clock() - self._last_login
            if waited < cooldown:
                raise SessionTakeoverError(
                    "the account session is in use by another client; "
                    f"retrying in {int(cooldown - waited)}s"
                )
        return await self.async_login()

    async def oem(self, path: str, payload: Any = None) -> dict[str, Any]:
        """Authenticated OEM request with one re-login retry on auth failure."""
        token = await self.async_get_token()
        try:
            result = await self._transport.oem_request(path, payload, access_token=token)
        except AuthError:
            token = await self._async_recover()
            result = await self._transport.oem_request(path, payload, access_token=token)
        return result

    async def tob(self, path: str, payload: Any = None) -> dict[str, Any]:
        """Authenticated ToB request with one re-login retry on auth failure."""
        token = await self.async_get_token()
        try:
            result = await self._transport.tob_request(path, payload, access_token=token)
        except AuthError:
            token = await self._async_recover()
            result = await self._transport.tob_request(path, payload, access_token=token)
        return result
