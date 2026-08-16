"""Async HTTP transport for the HolaBrain (dollin) cloud.

The transport owns request framing (headers, signing, serialization) and error mapping. It
receives its ``httpx.AsyncClient`` by dependency injection so tests can drive it with
``respx`` and so the same code works inside and outside Home Assistant.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx

from ..auth.signer import oem_sign, tob_sign
from ..const import (
    APP_ID,
    APP_KEY,
    APP_SECRET,
    APP_VERSION,
    CLIENT_ID,
    CLIENT_SECRET,
    DEFAULT_REGION,
    DEFAULT_TIMEOUT,
    REGION_HOSTS,
    SIGNATURE_VERSION,
)
from ..exceptions import (
    ApiError,
    AuthError,
    CredentialsRejectedError,
    NetworkError,
    RateLimitError,
    TokenExpiredError,
)
from ..redact import redact_path

_LOGGER = logging.getLogger(__name__)


def _ms(started: float) -> float:
    return (time.monotonic() - started) * 1000


# The token is stale. Logging in again fixes it, and nothing is competing for the account.
# 12001 is confirmed, not inferred: a live account answered it with "Token has expired" while
# Home Assistant looped on setup, unable to recover because the code was in none of these
# sets and so never became an AuthError at all.
_TOKEN_EXPIRED_CODES = frozenset({12001})
# The account or the password itself was refused. Logging in again would resend the same
# rejected credentials, so the caller must stop and ask the user.
_CREDENTIALS_REJECTED_CODES = frozenset({3114016})
# Auth failures whose meaning is not known. They keep the conservative reading — assume the
# session was taken over by another client — because that path backs off instead of looping.
#
# 14005 sits here rather than in the expiry set. It was put there as a guess, reconstructed
# from this project's own fixtures, standing in for the code we have now identified as 12001.
# With the real one known, the guess has no evidence left supporting it, and what evidence
# there is points the other way: the only place 14005 appears with a message from the cloud,
# it reads "unusual activity" — the wording of a takeover, not an expiry. The conservative
# branch still recovers, it just serves the cool-down first.
_AUTH_CODES = frozenset({5, 14005}) | _TOKEN_EXPIRED_CODES | _CREDENTIALS_REJECTED_CODES
_RATE_LIMIT_CODES = frozenset({4001, 429})


def generate_device_id() -> str:
    """Return a fresh, stable-per-install device id for the ``deviceId`` header.

    The caller is expected to persist this and reuse it; the cloud treats a changing device
    id as a new client.
    """
    return "hb" + uuid.uuid4().hex[:30]


def _dumps(payload: Any) -> str:
    """Serialize a request body exactly as it will be signed and sent.

    Compact separators and ``ensure_ascii=False`` are mandatory: the signature is computed
    over these bytes, so any reformatting would break it. ``None`` becomes ``{}``.
    """
    if payload is None:
        return "{}"
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


class HttpTransport:
    """Signed request transport for both the OEM (`/v1/*`) and ToB (`/midea/open/*`) APIs."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        region: str = DEFAULT_REGION,
        device_id: str | None = None,
        language: str = "en_US",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if region not in REGION_HOSTS:
            raise ValueError(f"unknown region {region!r}")
        self._client = client
        self._region = region
        self._base = REGION_HOSTS[region]
        self._device_id = device_id or generate_device_id()
        self._language = language
        self._timeout = timeout

    @property
    def region(self) -> str:
        return self._region

    @property
    def device_id(self) -> str:
        return self._device_id

    async def oem_request(
        self, path: str, payload: Any = None, *, access_token: str = ""
    ) -> dict[str, Any]:
        """POST to a legacy `/v1/*` endpoint using the OEM signature."""
        body = _dumps(payload)
        now = int(time.time() * 1000)
        random = str(now)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "accessToken": access_token,
            "currentTimeMillis": str(now),
            "User-Agent": f"OEM/{APP_VERSION} (holabrain-ha)",
            "sign": oem_sign(APP_KEY, APP_SECRET, body, random),
            "random": random,
            "secretVersion": "1",
            "language": self._language,
            "clientType": "1",
            "reqId": uuid.uuid4().hex,
            "deviceId": self._device_id,
            "appVNum": APP_VERSION,
            "appId": APP_ID,
            "stamp": _stamp(now),
        }
        return await self._send(path, body, headers)

    async def tob_request(
        self, path: str, payload: Any = None, *, access_token: str
    ) -> dict[str, Any]:
        """POST to a business `/midea/open/business/v1/*` endpoint using ToB v2.0."""
        body = _dumps(payload)
        now = int(time.time() * 1000)
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "ClientId": CLIENT_ID,
            "Authorization": f"Bearer {access_token}",
            "SignatureVersion": SIGNATURE_VERSION,
            "Signature": tob_sign(CLIENT_SECRET, "POST", path, body),
            "reqId": str(uuid.uuid4()),
            "appVNum": APP_VERSION,
            "appId": APP_ID,
            "language": self._language,
            "stamp": _stamp(now),
            "currentTimeMillis": str(now),
        }
        return await self._send(path, body, headers)

    async def _send(self, path: str, body: str, headers: dict[str, str]) -> dict[str, Any]:
        url = self._base + path
        # Every message about this request names the appliance-free form of the path: the
        # device endpoints carry the appliance id as their last segment, and these strings
        # end up in log files that are attached to public issues.
        safe = redact_path(path)
        started = time.monotonic()
        try:
            response = await self._client.post(
                url, content=body.encode(), headers=headers, timeout=self._timeout
            )
        except httpx.HTTPError as err:
            _LOGGER.debug("%s failed after %.0f ms: %s", safe, _ms(started), err)
            raise NetworkError(f"request to {safe} failed: {err}") from err

        _LOGGER.debug(
            "%s -> HTTP %s in %.0f ms", safe, response.status_code, _ms(started)
        )
        if response.status_code in (401, 403):
            raise AuthError(f"{safe} returned HTTP {response.status_code}")
        if response.status_code == 429:
            raise RateLimitError(f"{safe} rate limited", code=429)

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as err:
            raise ApiError(f"{safe} returned non-JSON (HTTP {response.status_code})") from err
        if not isinstance(data, dict):
            raise ApiError(f"{safe} returned unexpected payload type {type(data).__name__}")

        code = data.get("code")
        if code not in (0, None):
            # The business code is what decides whether this becomes an auth failure, a
            # throttle or a plain error, and an unmapped one is the single most useful thing
            # a report can carry — the cloud's own text names the symptom, never the number.
            _LOGGER.debug("%s answered business code %s: %s", safe, code, data.get("msg"))
        if code is None and not response.is_success:
            # Some gateway errors answer with a JSON body that carries no business code at
            # all. Without this check they would fall into the permissive branch below and
            # reach callers as a *successful* response with no data — a 404 on a wrong path
            # becomes an empty device inventory (and every device is forgotten), and a write
            # that never happened is reported as done. The status and the path are in the
            # message because that pair is the only thing that identifies such a failure.
            raise ApiError(f"{safe} returned HTTP {response.status_code} without a code")
        if code in (0, None):
            return data
        message = str(data.get("msg") or data.get("message") or "").strip() or f"code {code}"
        if code in _TOKEN_EXPIRED_CODES:
            raise TokenExpiredError(message)
        if code in _CREDENTIALS_REJECTED_CODES:
            raise CredentialsRejectedError(message)
        if code in _AUTH_CODES:
            raise AuthError(message)
        if code in _RATE_LIMIT_CODES:
            raise RateLimitError(message, code=code)
        raise ApiError(message, code=code)


def _stamp(now_ms: int) -> str:
    return time.strftime("%Y%m%d%H%M%S", time.gmtime(now_ms / 1000))
