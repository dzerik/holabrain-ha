# Expired-token re-login and manual token refresh — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the cloud's auth business codes into "token expired", "credentials rejected" and "unknown", so an expired token is re-logged-in immediately instead of being charged the session-takeover back-off, and give the user an explicit way to force a fresh login.

**Architecture:** Two new `AuthError` subclasses are raised by the HTTP transport based on the business code. `AuthManager` grows a third branch: expiry re-logs in without touching the eviction counters, rejected credentials propagate without attempting a login at all, and anything else keeps today's takeover path. A shared `_async_relogin()` serves both the expiry branch and a new public `async_refresh_token()`, which additionally resets the back-off state. The Home Assistant layer exposes that as a `holabrain.refresh_token` service and a disabled-by-default button on the account device.

**Tech Stack:** Python 3.12+, Home Assistant 2025.3+, httpx, pytest + pytest-asyncio (`asyncio_mode = "auto"`), `pytest-homeassistant-custom-component`, ruff.

Spec: `docs/superpowers/specs/2026-08-09-token-refresh-design.md`

## Global Constraints

- **`custom_components/holabrain/aiodollin/` must never import Home Assistant.** Tasks 1–3 touch that package; `tests/aiodollin/test_no_ha_imports.py` fails the build otherwise.
- **Line length 100.** Ruff lint selects `E, F, W, I, UP, B, SIM, RUF`. Run `ruff check custom_components tests scripts` before every commit.
- **Do not run `ruff format` over whole files.** It is not a CI gate; format only the lines you touch.
- **`from __future__ import annotations` at the top of every module you create or edit.**
- **Version lives in two files that must never disagree**: `version` in `pyproject.toml` and in `custom_components/holabrain/manifest.json`. The final version for this feature is `0.15.0` (minor: a new service and a new entity). It is bumped once, in Task 7 — do not bump it in earlier tasks.
- **`strings.json` is the translation source of truth.** Any key added there must be mirrored into all five of `translations/{en,ru,be,kk,uz}.json` plus `icons.json`, and `python scripts/check_translations.py` must pass.
- **Tests never touch the network.** `tests/aiodollin/` uses fake transports or `respx` and must not import Home Assistant fixtures; `tests/` uses the shared fixtures in `tests/conftest.py`.
- **Commits use Conventional Commits** with an imperative subject describing the user-visible effect.
- **Coverage is on by default** (`addopts` in `pyproject.toml`). Add `--no-cov` when running a single test for speed.
- The current version at the start of this plan is `0.14.2`.

---

## File Structure

**Created:**
- nothing — every change lands in an existing module.

**Modified:**
- `custom_components/holabrain/aiodollin/exceptions.py` — two new `AuthError` subclasses.
- `custom_components/holabrain/aiodollin/transport/http.py` — split `_AUTH_CODES` into three sets and raise the matching class.
- `custom_components/holabrain/aiodollin/__init__.py` — export the new classes.
- `custom_components/holabrain/aiodollin/auth/manager.py` — the three-branch retry, `_async_relogin()`, `async_refresh_token()`.
- `custom_components/holabrain/aiodollin/client.py` — façade method.
- `custom_components/holabrain/coordinator.py` — `async_refresh_token()` mapping cloud errors onto Home Assistant ones.
- `custom_components/holabrain/services.py` — the `refresh_token` service.
- `custom_components/holabrain/services.yaml` — its target selectors.
- `custom_components/holabrain/account.py` — `HolabrainRefreshTokenButton`.
- `custom_components/holabrain/button.py` — add the button to the platform.
- `custom_components/holabrain/strings.json`, `icons.json`, `translations/{en,ru,be,kk,uz}.json` — strings for the service, the button and the failure message.
- `pyproject.toml`, `custom_components/holabrain/manifest.json`, `CHANGELOG.md`, `README.md`, `docs/accounts.md` — release chores.

**Tests modified:**
- `tests/aiodollin/test_http.py` — code-to-class mapping.
- `tests/aiodollin/test_auth.py` — the three retry branches.
- `tests/aiodollin/test_session_takeover.py` — the back-off is not charged for an expiry; a manual refresh ignores the cool-down.
- `tests/test_coordinator.py` — the coordinator's error mapping.
- `tests/test_services.py` — the service.
- `tests/test_account_entities.py` — the button.

---

### Task 1: Tell the auth business codes apart

The transport is the only place that knows cloud business codes. It currently collapses three of them into one `AuthError`, which is why the auth layer cannot react differently to an expired token and a wrong password.

**Files:**
- Modify: `custom_components/holabrain/aiodollin/exceptions.py`
- Modify: `custom_components/holabrain/aiodollin/transport/http.py:33` and `:157-158`
- Modify: `custom_components/holabrain/aiodollin/__init__.py`
- Test: `tests/aiodollin/test_http.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `TokenExpiredError(AuthError)` and `CredentialsRejectedError(AuthError)`, importable from `custom_components.holabrain.aiodollin.exceptions` and re-exported from `custom_components.holabrain.aiodollin`. Raised by `HttpTransport._send` for business codes `14005` and `3114016` respectively.

- [ ] **Step 1: Write the failing test**

Append to `tests/aiodollin/test_http.py`:

```python
@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (14005, TokenExpiredError),
        (3114016, CredentialsRejectedError),
        (5, AuthError),
    ],
)
@pytest.mark.asyncio
async def test_auth_codes_are_told_apart(code, expected):
    """The auth layer reacts differently to each of these, so the code must survive.

    An expired token is re-logged-in immediately; rejected credentials must never trigger
    another login; an unrecognised code keeps the conservative takeover handling.
    """
    client = _client(lambda r: httpx.Response(200, json={"code": code, "msg": "x"}))
    transport = HttpTransport(client, region="eu")
    with pytest.raises(expected) as excinfo:
        await transport.oem_request("/v1/x")
    assert type(excinfo.value) is expected
    await client.aclose()
```

Add the two names to the existing import block at the top of the file:

```python
from custom_components.holabrain.aiodollin.exceptions import (
    ApiError,
    AuthError,
    CredentialsRejectedError,
    NetworkError,
    RateLimitError,
    TokenExpiredError,
)
```

`assert type(...) is expected` is the point of the test: `pytest.raises(AuthError)` would pass for every subclass and prove nothing.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/aiodollin/test_http.py::test_auth_codes_are_told_apart -v --no-cov`
Expected: collection error — `ImportError: cannot import name 'CredentialsRejectedError'`.

- [ ] **Step 3: Add the exception classes**

In `custom_components/holabrain/aiodollin/exceptions.py`, insert after `SessionTakeoverError` (keeping the `AuthError` subclasses together):

```python
class TokenExpiredError(AuthError):
    """The access token is no longer accepted; a fresh login fixes it.

    Distinct from a plain :class:`AuthError` because nothing is actually wrong — tokens
    have a lifetime. Callers may log in again straight away, without the back-off that
    exists to stop two clients fighting over the account's single session.
    """


class CredentialsRejectedError(AuthError):
    """The cloud refused the account or the password itself.

    Logging in again would resend exactly the credentials that were just rejected, so
    callers must not: the only fix is the user supplying new ones, and retrying risks
    locking the account.
    """
```

- [ ] **Step 4: Split the code sets in the transport**

In `custom_components/holabrain/aiodollin/transport/http.py`, replace line 33:

```python
_AUTH_CODES = frozenset({5, 14005, 3114016})
```

with:

```python
# The token is stale. Logging in again fixes it, and nothing is competing for the account.
_TOKEN_EXPIRED_CODES = frozenset({14005})
# The account or the password itself was refused. Logging in again would resend the same
# rejected credentials, so the caller must stop and ask the user.
_CREDENTIALS_REJECTED_CODES = frozenset({3114016})
# Auth failures whose meaning is not known. They keep the conservative reading — assume the
# session was taken over by another client — because that path backs off instead of looping.
_AUTH_CODES = frozenset({5}) | _TOKEN_EXPIRED_CODES | _CREDENTIALS_REJECTED_CODES
```

Then replace lines 157-158:

```python
        if code in _AUTH_CODES:
            raise AuthError(message)
```

with:

```python
        if code in _TOKEN_EXPIRED_CODES:
            raise TokenExpiredError(message)
        if code in _CREDENTIALS_REJECTED_CODES:
            raise CredentialsRejectedError(message)
        if code in _AUTH_CODES:
            raise AuthError(message)
```

Extend the exception import at the top of the same file:

```python
from ..exceptions import (
    ApiError,
    AuthError,
    CredentialsRejectedError,
    NetworkError,
    RateLimitError,
    TokenExpiredError,
)
```

(The existing import is a one-line `from ..exceptions import ApiError, AuthError, NetworkError, RateLimitError` — replace it wholesale; the parenthesised form is what ruff's isort profile produces once it exceeds 100 characters.)

- [ ] **Step 5: Export the new classes**

In `custom_components/holabrain/aiodollin/__init__.py`, extend the `from .exceptions import (...)` block with `CredentialsRejectedError` and `TokenExpiredError` (alphabetical inside the block), and add both to `__all__` keeping it sorted: `"CredentialsRejectedError"` goes between `"Claimable"` and `"Device"`, `"TokenExpiredError"` between `"StaticVariant"` and `"TokenStore"`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/aiodollin/test_http.py -v --no-cov`
Expected: PASS, including the pre-existing `test_business_error_codes_map_to_exceptions` — it asserts `pytest.raises(AuthError)`, which subclasses still satisfy.

- [ ] **Step 7: Verify the core isolation invariant and lint**

Run: `pytest tests/aiodollin/test_no_ha_imports.py --no-cov && ruff check custom_components tests scripts`
Expected: both PASS.

- [ ] **Step 8: Commit**

```bash
git add custom_components/holabrain/aiodollin/exceptions.py \
        custom_components/holabrain/aiodollin/transport/http.py \
        custom_components/holabrain/aiodollin/__init__.py \
        tests/aiodollin/test_http.py
git commit -m "refactor: tell an expired token apart from rejected credentials"
```

---

### Task 2: Re-login on an expired token without charging the takeover back-off

`_async_recover()` currently handles every `AuthError`, so a token that merely reached the end of its life increments `_evictions`. `LOGIN_BACKOFF_SECONDS[0]` is `0.0`, so the first one still recovers — but the counter stays raised for `EVICTION_FORGET_SECONDS` (30 minutes), and the poll runs every 60 seconds. A few natural expiries inside that window escalate the cool-down to 60 s, 300 s, 900 s, at which point `_async_recover` raises `SessionTakeoverError` instead of logging in and the poll is abandoned. That is the bug.

**Files:**
- Modify: `custom_components/holabrain/aiodollin/auth/manager.py`
- Test: `tests/aiodollin/test_auth.py`, `tests/aiodollin/test_session_takeover.py`

**Interfaces:**
- Consumes: `TokenExpiredError`, `CredentialsRejectedError` from Task 1.
- Produces: `AuthManager._async_relogin() -> str` (private, used by Task 3). `AuthManager.oem()` / `tob()` keep their signatures `(path: str, payload: Any = None) -> dict[str, Any]` and their one-retry contract.

- [ ] **Step 1: Write the failing tests**

Append to `tests/aiodollin/test_auth.py`:

```python
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
```

Extend that file's import of the exception module:

```python
from custom_components.holabrain.aiodollin.exceptions import (
    AuthError,
    CredentialsRejectedError,
    TokenExpiredError,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/aiodollin/test_auth.py -v --no-cov`
Expected: the five new tests FAIL — `test_an_expiry_is_not_counted_as_a_session_takeover` with `assert 1 == 0`, `test_rejected_credentials_never_trigger_another_login` with `assert 3 == 1` (the manager logged in and retried), and the others on the same recovery path.

- [ ] **Step 3: Add the shared re-login helper**

In `custom_components/holabrain/aiodollin/auth/manager.py`, insert before `_async_recover`:

```python
    async def _async_relogin(self) -> str:
        """Drop the current session and mint a new one.

        The stored session is cleared *before* the login rather than overwritten after it:
        a login that fails must not leave a token behind that :meth:`async_get_token`
        would later hand out as usable.
        """
        self._token = None
        await self._store.clear()
        return await self.async_login()
```

- [ ] **Step 4: Route the three branches**

Replace the bodies of `oem` and `tob` with:

```python
    async def oem(self, path: str, payload: Any = None) -> dict[str, Any]:
        """Authenticated OEM request, retried once if the token turns out to be unusable."""
        token = await self.async_get_token()
        try:
            result = await self._transport.oem_request(path, payload, access_token=token)
        except CredentialsRejectedError:
            # The credentials themselves were refused. Logging in would resend them.
            raise
        except TokenExpiredError:
            token = await self._async_relogin()
            result = await self._transport.oem_request(path, payload, access_token=token)
        except AuthError:
            token = await self._async_recover()
            result = await self._transport.oem_request(path, payload, access_token=token)
        return result

    async def tob(self, path: str, payload: Any = None) -> dict[str, Any]:
        """Authenticated ToB request, retried once if the token turns out to be unusable."""
        token = await self.async_get_token()
        try:
            result = await self._transport.tob_request(path, payload, access_token=token)
        except CredentialsRejectedError:
            raise
        except TokenExpiredError:
            token = await self._async_relogin()
            result = await self._transport.tob_request(path, payload, access_token=token)
        except AuthError:
            token = await self._async_recover()
            result = await self._transport.tob_request(path, payload, access_token=token)
        return result
```

Order matters: `CredentialsRejectedError` and `TokenExpiredError` are both `AuthError` subclasses, so their handlers must come first. The retried call is outside every `except`, so a second failure propagates instead of recursing.

Extend the module's exception import:

```python
from ..exceptions import (
    AuthError,
    CredentialsRejectedError,
    SessionTakeoverError,
    TokenExpiredError,
)
```

- [ ] **Step 5: Correct the two comments this invalidates**

The comment above `LOGIN_BACKOFF_SECONDS` says the first retry is immediate because "it is usually just an expired token" — expiry now has its own branch. Replace that comment block with:

```python
# Cool-down before reclaiming a session that was taken over, indexed by how many takeovers
# happened recently. The first one retries immediately, because a single unexplained
# rejection is usually a one-off. Further ones back off: a takeover shortly after we
# reclaimed the session means another client is actively using the account, and racing it
# helps nobody. An expired token does not come through here at all — see `_async_relogin`.
```

In `_async_recover`'s docstring, replace the first line with:

```python
        """Handle a token rejected for a reason we cannot name: assume it was taken over.
```

Also update the module docstring's second paragraph, which still claims every `AuthError` leads to one re-login:

```python
Those attach the current token and, on a rejection, recover once and retry. How they recover
depends on why the token was refused: an expired one is replaced immediately, a session taken
over by another client is reclaimed under a cool-down, and refused credentials stop the call.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/aiodollin/test_auth.py tests/aiodollin/test_session_takeover.py -v --no-cov`
Expected: PASS. The takeover tests raise a bare `AuthError("We've detected unusual activity on your account")`, which still lands on `_async_recover`, so they are unaffected.

- [ ] **Step 7: Add the regression test for the escalation this fixes**

Append to `tests/aiodollin/test_session_takeover.py`:

```python
@pytest.mark.asyncio
async def test_expiries_do_not_escalate_the_cool_down_for_a_later_takeover():
    """Regression: the poll runs every minute and the counter forgets after thirty.

    Before this, a handful of ordinary token expiries inside that window pushed the
    cool-down to fifteen minutes, and the next genuine takeover was refused outright
    instead of being reclaimed.
    """
    clock = FakeClock()
    cloud = Cloud()
    manager = _manager(cloud, clock)
    await manager.oem("/v1/thing")

    # Three ordinary expiries inside the forget window — this is the path `oem` takes on a
    # TokenExpiredError, without needing a transport that can raise one.
    for _ in range(3):
        await manager._async_relogin()

    assert manager.evictions == 0

    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")  # first takeover: must still be reclaimed immediately
```

- [ ] **Step 8: Run it**

Run: `pytest tests/aiodollin/test_session_takeover.py -v --no-cov`
Expected: PASS.

- [ ] **Step 9: Lint and commit**

```bash
ruff check custom_components tests scripts
git add custom_components/holabrain/aiodollin/auth/manager.py \
        tests/aiodollin/test_auth.py tests/aiodollin/test_session_takeover.py
git commit -m "fix: an expired token is re-logged-in instead of counted as a takeover"
```

---

### Task 3: A public way to force a fresh login

**Files:**
- Modify: `custom_components/holabrain/aiodollin/auth/manager.py`
- Modify: `custom_components/holabrain/aiodollin/client.py`
- Test: `tests/aiodollin/test_session_takeover.py`

**Interfaces:**
- Consumes: `AuthManager._async_relogin()` from Task 2.
- Produces: `AuthManager.async_refresh_token() -> str` and `DollinClient.async_refresh_token() -> str`. Both return the new access token and raise `AuthError` (or a subclass) if the login fails.

- [ ] **Step 1: Write the failing test**

Append to `tests/aiodollin/test_session_takeover.py`:

```python
@pytest.mark.asyncio
async def test_a_manual_refresh_ignores_the_cool_down():
    """The back-off protects the account from the integration's own initiative.

    A person pressing "refresh token" is not the integration's initiative — they have
    decided the session is worth taking, and a silent no-op would look like a broken
    button.
    """
    clock = FakeClock()
    cloud = Cloud()
    manager = _manager(cloud, clock)
    await manager.oem("/v1/thing")

    # Two takeovers in a row put the manager deep inside a cool-down.
    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")
    cloud.other_client_logs_in()
    with pytest.raises(SessionTakeoverError):
        await manager.oem("/v1/thing")

    logins_before = cloud.logins
    token = await manager.async_refresh_token()

    assert cloud.logins == logins_before + 1
    assert token == cloud.live
    # The cool-down state is cleared, so the next poll is not still serving the old debt.
    assert manager.evictions == 0
    await manager.oem("/v1/thing")  # must not raise
```

Extend that file's exception import:

```python
from custom_components.holabrain.aiodollin.exceptions import AuthError, SessionTakeoverError
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/aiodollin/test_session_takeover.py::test_a_manual_refresh_ignores_the_cool_down -v --no-cov`
Expected: FAIL with `AttributeError: 'AuthManager' object has no attribute 'async_refresh_token'`.

- [ ] **Step 3: Implement it on the manager**

In `custom_components/holabrain/aiodollin/auth/manager.py`, add after `async_login`:

```python
    async def async_refresh_token(self) -> str:
        """Mint a new session because the user asked for one.

        The takeover cool-down is reset rather than respected: it exists to stop the
        integration racing the mobile app on its own initiative, and this is not the
        integration's initiative. Leaving the counter raised would also make the next few
        polls pay for a debt the user has just settled by hand.
        """
        self._evictions = 0
        self._last_eviction = None
        return await self._async_relogin()
```

- [ ] **Step 4: Expose it on the façade**

In `custom_components/holabrain/aiodollin/client.py`, add after `_async_status`:

```python
    async def async_refresh_token(self) -> str:
        """Discard the current session and log in again. Claims the account's one session."""
        return await self._auth.async_refresh_token()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/aiodollin --no-cov -q`
Expected: PASS, all of them.

- [ ] **Step 6: Lint and commit**

```bash
ruff check custom_components tests scripts
git add custom_components/holabrain/aiodollin/auth/manager.py \
        custom_components/holabrain/aiodollin/client.py \
        tests/aiodollin/test_session_takeover.py
git commit -m "feat: aiodollin can mint a fresh session on demand"
```

---

### Task 4: Coordinator-level refresh with Home Assistant error mapping

The coordinator is where `aiodollin` exceptions become Home Assistant ones. A manual refresh that fails because the password changed should start the re-authentication flow — the same outcome a failed poll produces — and tell the user why.

**Files:**
- Modify: `custom_components/holabrain/coordinator.py`
- Modify: `custom_components/holabrain/strings.json` (an `exceptions` key — `icons.json` has no entry for those)
- Modify: `custom_components/holabrain/translations/{en,ru,be,kk,uz}.json`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Consumes: `DollinClient.async_refresh_token()` from Task 3.
- Produces: `HolabrainCoordinator.async_refresh_token() -> None`. Raises `homeassistant.exceptions.HomeAssistantError` with `translation_key="token_refresh_failed"` and the placeholder `{error}` on any `DollinError`; starts the entry's reauth flow first when the failure is an `AuthError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_coordinator.py`:

```python
async def test_a_manual_token_refresh_logs_in_again(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """The button and the service exist to get a wedged session unstuck."""
    assert await setup_integration()
    coordinator = config_entry.runtime_data.coordinator
    logins_before = cloud.logins

    await coordinator.async_refresh_token()

    assert cloud.logins == logins_before + 1


async def test_a_refusal_of_the_credentials_asks_the_user_to_sign_in_again(
    hass: HomeAssistant, setup_integration, config_entry, cloud: FakeCloud
) -> None:
    """A password changed in the mobile app is the common cause. Failing silently would
    leave the user pressing a button that never works and no way to find out why."""
    assert await setup_integration()
    coordinator = config_entry.runtime_data.coordinator
    cloud.fail_next(FakeCloud.LOGIN, {"code": 3114016, "msg": "wrong credentials"})

    with pytest.raises(HomeAssistantError):
        await coordinator.async_refresh_token()

    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert any(flow["context"].get("source") == "reauth" for flow in flows)
```

Ensure the file imports what these need — `pytest`, `HomeAssistantError` from `homeassistant.exceptions`, `DOMAIN` from `custom_components.holabrain.const`, and `FakeCloud` from `tests.conftest`. Add whichever are missing to the existing import block.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_coordinator.py -k token_refresh --no-cov -v`
Expected: FAIL with `AttributeError: 'HolabrainCoordinator' object has no attribute 'async_refresh_token'`.

- [ ] **Step 3: Implement the coordinator method**

In `custom_components/holabrain/coordinator.py`, add directly after `async_refresh_now`:

```python
    async def async_refresh_token(self) -> None:
        """Sign in again with the stored credentials, whatever the current session says.

        This is the escape hatch for a session that is wedged: the cloud is holding a token
        it will not accept and nothing the integration does on its own is dislodging it.
        Deliberately does not poll afterwards — `async_refresh_now` already exists for that,
        and one button doing two account-costing things hides which one failed.
        """
        try:
            await self._client.async_refresh_token()
        except AuthError as err:
            # The credentials themselves are the problem, so no amount of retrying helps.
            # Home Assistant's re-authentication flow is the only way out; start it, then
            # still raise, because the caller pressed a button and deserves an answer.
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="token_refresh_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        except DollinError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="token_refresh_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        _LOGGER.debug("account token refreshed by request for entry %s", self.config_entry.entry_id)
```

The last line exceeds 100 characters — split it:

```python
        _LOGGER.debug(
            "account token refreshed by request for entry %s", self.config_entry.entry_id
        )
```

Add `HomeAssistantError` to the existing `from homeassistant.exceptions import ...` at `coordinator.py:41`.

- [ ] **Step 4: Add the failure message to `strings.json`**

Under the top-level `"exceptions"` object, add:

```json
    "token_refresh_failed": { "message": "Could not refresh the account token: {error}" }
```

- [ ] **Step 5: Mirror it into all five translations**

`translations/en.json` — `"token_refresh_failed": {"message": "Could not refresh the account token: {error}"}`

`translations/ru.json` — `"token_refresh_failed": {"message": "Не удалось обновить токен аккаунта: {error}"}`

`translations/be.json` — `"token_refresh_failed": {"message": "Не ўдалося абнавіць токен акаўнта: {error}"}`

`translations/kk.json` — `"token_refresh_failed": {"message": "Есептік жазба токенін жаңарту мүмкін болмады: {error}"}`

`translations/uz.json` — `"token_refresh_failed": {"message": "Hisob tokenini yangilab bo'lmadi: {error}"}`

Each goes into that file's own `"exceptions"` object. The `{error}` placeholder must appear in every one — `scripts/check_translations.py` compares the placeholder sets and fails on a mismatch.

- [ ] **Step 6: Run the tests and the translation check**

Run: `pytest tests/test_coordinator.py -k token_refresh -v --no-cov && python scripts/check_translations.py`
Expected: both PASS.

- [ ] **Step 7: Lint and commit**

```bash
ruff check custom_components tests scripts
git add custom_components/holabrain/coordinator.py \
        custom_components/holabrain/strings.json \
        custom_components/holabrain/translations \
        tests/test_coordinator.py
git commit -m "feat: the coordinator can mint a fresh account token on request"
```

---

### Task 5: The `holabrain.refresh_token` service

**Files:**
- Modify: `custom_components/holabrain/services.py`
- Modify: `custom_components/holabrain/services.yaml`
- Modify: `custom_components/holabrain/strings.json`, `custom_components/holabrain/icons.json`
- Modify: `custom_components/holabrain/translations/{en,ru,be,kk,uz}.json`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: `HolabrainCoordinator.async_refresh_token()` from Task 4.
- Produces: the service `holabrain.refresh_token`, accepting the optional fields `config_entry_id` and `device_id` (the existing `_REFRESH_SCHEMA`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_services.py`:

```python
async def test_refresh_token_signs_in_again(
    hass: HomeAssistant, setup_integration, cloud: FakeCloud
) -> None:
    """The service is the supported way out of a session the cloud will not accept."""
    assert await setup_integration()
    logins_before = cloud.logins

    await hass.services.async_call(DOMAIN, "refresh_token", {}, blocking=True)
    await hass.async_block_till_done()

    assert cloud.logins == logins_before + 1


async def test_refresh_token_says_so_when_no_account_is_loaded(
    hass: HomeAssistant,
) -> None:
    """An automation referencing the action keeps validating while the entry is unloaded,
    so the call itself has to explain why nothing happened."""
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "refresh_token", {}, blocking=True)
```

The second test needs the service registered without a loaded entry. Services are registered in `async_setup` and stay registered, so this passes as long as the integration has been set up once in the session; if the test fails at `ServiceNotFound`, add the `setup_integration` fixture and unload the entry first with
`await hass.config_entries.async_unload(config_entry.entry_id)`.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_services.py -k refresh_token -v --no-cov`
Expected: FAIL with `ServiceNotFound: Service holabrain.refresh_token not found`.

- [ ] **Step 3: Register the service**

In `custom_components/holabrain/services.py`, add the constant next to the others:

```python
SERVICE_REFRESH_TOKEN = "refresh_token"
```

Add the handler next to `_async_scan`, inside `async_setup_services`:

```python
    async def _async_refresh_token(call: ServiceCall) -> None:
        entries = _target_entries(hass, call)
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_loaded_account"
            )
        for entry in entries:
            coordinator = entry.runtime_data.coordinator
            # Raises HomeAssistantError with the reason; nothing to add here.
            await coordinator.async_refresh_token()
```

And register it beside the existing two:

```python
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH_TOKEN, _async_refresh_token, schema=_REFRESH_SCHEMA
    )
```

Extend the module docstring with a paragraph after the `refresh_capabilities` one:

```
``holabrain.refresh_token`` signs in again with the stored credentials and replaces the
account token. The integration recovers from an expired token on its own, so this is for the
case it cannot: a session the cloud keeps refusing. Like every account request it claims the
account's only session, which signs the vendor's mobile app out.
```

- [ ] **Step 4: Declare the fields in `services.yaml`**

Append to `custom_components/holabrain/services.yaml`:

```yaml
refresh_token:
  fields:
    config_entry_id:
      required: false
      selector:
        config_entry:
          integration: holabrain
    device_id:
      required: false
      selector:
        device:
          integration: holabrain
```

- [ ] **Step 5: Add the strings**

In `strings.json`, under `"services"`:

```json
    "refresh_token": {
      "name": "Refresh account token",
      "description": "Signs in again with the stored credentials and replaces the account token. The integration replaces an expired token by itself, so reach for this when a session is stuck. It claims the account's only session and signs the HolaBrain mobile app out.",
      "fields": {
        "config_entry_id": {
          "name": "Account",
          "description": "Limit the refresh to one HolaBrain account. All accounts are refreshed when omitted."
        },
        "device_id": {
          "name": "Device",
          "description": "Limit the refresh to the account this device belongs to."
        }
      }
    }
```

In `icons.json`, under `"services"`, add `"refresh_token": { "service": "mdi:key-change" }` (keep the object alphabetically ordered: it goes between `refresh_capabilities` and `rename_device`).

- [ ] **Step 6: Mirror the service strings into all five translations**

Each file's `"services"` object gets a `"refresh_token"` entry with the same shape. Field names and descriptions are reused verbatim from that file's `refresh_capabilities` entry, which already says exactly the right thing.

`en.json` — name `"Refresh account token"`, description as in `strings.json`.

`ru.json` — name `"Обновить токен аккаунта"`, description: `"Выполняет повторный вход с сохранёнными учётными данными и заменяет токен аккаунта. Протухший токен интеграция заменяет сама, так что это средство для застрявшей сессии. Действие занимает единственную сессию аккаунта и выкидывает из мобильного приложения HolaBrain."`; fields `"Аккаунт"` / `"Ограничить обновление одним аккаунтом HolaBrain. Без указания обновляются все аккаунты."` and `"Устройство"` / `"Ограничить обновление аккаунтом, которому принадлежит это устройство."`

`be.json` — name `"Абнавіць токен акаўнта"`, description: `"Выконвае паўторны ўваход з захаванымі ўліковымі данымі і замяняе токен акаўнта. Пратухлы токен інтэграцыя замяняе сама, таму гэта сродак для завіслай сесіі. Дзеянне займае адзіную сесію акаўнта і выкідвае з мабільнай праграмы HolaBrain."`; fields `"Уліковы запіс"` / `"Абмежаваць абнаўленне адным уліковым запісам HolaBrain. Без указання абнаўляюцца ўсе."` and `"Прылада"` / `"Абмежаваць абнаўленне ўліковым запісам, якому належыць гэтая прылада."`

`kk.json` — name `"Есептік жазба токенін жаңарту"`, description: `"Сақталған тіркелгі деректерімен қайта кіріп, есептік жазба токенін алмастырады. Мерзімі өткен токенді интеграция өзі алмастырады, сондықтан бұл — тұрып қалған сеансқа арналған құрал. Әрекет есептік жазбаның жалғыз сеансын иеленіп, HolaBrain мобильді қосымшасынан шығарады."`; fields `"Тіркелгі"` / `"Жаңартуды бір HolaBrain тіркелгісімен шектеу. Көрсетілмесе, барлығы жаңартылады."` and `"Құрылғы"` / `"Жаңартуды осы құрылғы тиесілі тіркелгімен шектеу."`

`uz.json` — name `"Hisob tokenini yangilash"`, description: `"Saqlangan hisob ma'lumotlari bilan qayta kiradi va hisob tokenini almashtiradi. Muddati o'tgan tokenni integratsiya o'zi almashtiradi, shuning uchun bu — qotib qolgan seans uchun vosita. Amal hisobning yagona seansini egallaydi va HolaBrain mobil ilovasidan chiqarib yuboradi."`; fields `"Hisob"` / `"Yangilashni bitta HolaBrain hisobi bilan cheklash. Ko'rsatilmasa, barchasi yangilanadi."` and `"Qurilma"` / `"Yangilashni ushbu qurilma tegishli bo'lgan hisob bilan cheklash."`

- [ ] **Step 7: Run the tests and the translation check**

Run: `pytest tests/test_services.py -k refresh_token -v --no-cov && python scripts/check_translations.py`
Expected: both PASS.

- [ ] **Step 8: Lint and commit**

```bash
ruff check custom_components tests scripts
git add custom_components/holabrain/services.py custom_components/holabrain/services.yaml \
        custom_components/holabrain/strings.json custom_components/holabrain/icons.json \
        custom_components/holabrain/translations tests/test_services.py
git commit -m "feat: add the refresh_token action"
```

---

### Task 6: The account button

**Files:**
- Modify: `custom_components/holabrain/account.py`
- Modify: `custom_components/holabrain/button.py:29`
- Modify: `custom_components/holabrain/strings.json`, `custom_components/holabrain/icons.json`
- Modify: `custom_components/holabrain/translations/{en,ru,be,kk,uz}.json`
- Test: `tests/test_account_entities.py`

**Interfaces:**
- Consumes: `HolabrainCoordinator.async_refresh_token()` from Task 4, `HolabrainAccountEntity` (existing).
- Produces: `HolabrainRefreshTokenButton(coordinator)` with unique id `f"{entry_id}_refresh_token"`, translation key `refresh_token`, added by the `button` platform.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_account_entities.py`:

```python
async def test_the_token_button_exists_but_is_not_offered_by_default(
    hass: HomeAssistant, setup_integration, config_entry
) -> None:
    """Pressing it signs the user out of the vendor's mobile app.

    That is a reasonable thing to ask for and an unreasonable thing to do by accident, so
    the button is registered but left disabled until someone goes looking for it.
    """
    assert await setup_integration()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "button", DOMAIN, f"{config_entry.entry_id}_refresh_token"
    )
    assert entity_id is not None

    entry = registry.async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert entry.entity_category is EntityCategory.DIAGNOSTIC
```

Add `from homeassistant.const import EntityCategory` to that file's imports.

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_account_entities.py -k token_button -v --no-cov`
Expected: FAIL with `assert None is not None`.

- [ ] **Step 3: Add the entity**

In `custom_components/holabrain/account.py`, append:

```python
class HolabrainRefreshTokenButton(HolabrainAccountEntity, ButtonEntity):
    """Sign in again and replace the account token.

    The integration replaces an expired token on its own, so this is not part of normal
    operation — it is the way out of a session the cloud keeps refusing. Disabled by
    default because pressing it claims the account's only session, and losing the mobile
    app's session is not something a stray tap should do.
    """

    _attr_translation_key = "refresh_token"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: HolabrainCoordinator) -> None:
        super().__init__(coordinator, "refresh_token")

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_token()
```

Add `from homeassistant.const import EntityCategory` to that module's imports, and extend the module docstring's last paragraph:

```
That is what lives here: a switch for the mode, a button for the one-off refresh that makes
cooperative mode practical, and a button that mints a new session when the current one is
wedged.
```

- [ ] **Step 4: Add it to the platform**

In `custom_components/holabrain/button.py`, change line 29 from:

```python
    async_add_entities([HolabrainRefreshButton(coordinator)])
```

to:

```python
    async_add_entities(
        [HolabrainRefreshButton(coordinator), HolabrainRefreshTokenButton(coordinator)]
    )
```

and extend the import to `from .account import HolabrainRefreshButton, HolabrainRefreshTokenButton`.

- [ ] **Step 5: Add the strings and the icon**

In `strings.json`, under `"entity"` → `"button"`, add `"refresh_token": { "name": "Refresh token" }`.
In `icons.json`, under `"entity"` → `"button"`, add `"refresh_token": { "default": "mdi:key-change" }`.

Translations, each under its own `"entity"` → `"button"`:
- `en.json` — `"refresh_token": {"name": "Refresh token"}`
- `ru.json` — `"refresh_token": {"name": "Обновить токен"}`
- `be.json` — `"refresh_token": {"name": "Абнавіць токен"}`
- `kk.json` — `"refresh_token": {"name": "Токенді жаңарту"}`
- `uz.json` — `"refresh_token": {"name": "Tokenni yangilash"}`

- [ ] **Step 6: Run the tests and the translation check**

Run: `pytest tests/test_account_entities.py -v --no-cov && python scripts/check_translations.py`
Expected: both PASS. The pre-existing `test_the_account_device_is_not_named_after_the_email` iterates only `("exclusive_mode", "refresh_now")`, so it is unaffected.

- [ ] **Step 7: Run the whole suite**

Run: `pytest`
Expected: PASS. `tests/test_platforms.py` and `tests/test_entity_metadata.py` count or enumerate entities in places; if either fails on the new button, update the expectation rather than hiding the entity.

- [ ] **Step 8: Lint and commit**

```bash
ruff check custom_components tests scripts
git add custom_components/holabrain/account.py custom_components/holabrain/button.py \
        custom_components/holabrain/strings.json custom_components/holabrain/icons.json \
        custom_components/holabrain/translations tests/test_account_entities.py
git commit -m "feat: add a disabled-by-default button that mints a fresh token"
```

---

### Task 7: Release chores

**Files:**
- Modify: `pyproject.toml:3`
- Modify: `custom_components/holabrain/manifest.json:21`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `docs/accounts.md`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: nothing code-facing.

- [ ] **Step 1: Bump the version in both files**

`pyproject.toml`: `version = "0.15.0"`. `custom_components/holabrain/manifest.json`: `"version": "0.15.0"`. A minor bump, because a new action and a new entity are backwards-compatible additions.

- [ ] **Step 2: Verify they agree**

Run: `grep -n '^version' pyproject.toml && grep -n '"version"' custom_components/holabrain/manifest.json`
Expected: both read `0.15.0`.

- [ ] **Step 3: Add the changelog entries**

Under `## [Unreleased]` in `CHANGELOG.md`:

```markdown
### Added

- Action `holabrain.refresh_token` and a disabled-by-default **Refresh token** button on the
  account device: sign in again with the stored credentials when a session is stuck. Both
  claim the account's only session and sign the mobile app out.

### Fixed

- An expired access token is now replaced immediately instead of being counted as a session
  taken over by another client. Ordinary token expiry no longer escalates the reclaim
  cool-down, which could leave a later poll refused outright.
- Credentials the cloud rejects no longer trigger another login attempt with the same
  password.
```

- [ ] **Step 4: Document the action in `README.md`**

The `## Services` section opens with `All four actions are available in **Developer tools → Actions** and in automations.` — change `four` to `five`.

Then add a subsection after the `### holabrain.scan_devices` block, following the shape of the existing ones (heading, prose, YAML example, note):

````markdown
### `holabrain.refresh_token`

Signs in again with the stored credentials and replaces the account token. The integration
replaces an expired token by itself, so reach for this only when a session is stuck — the
cloud holding a token it will not accept.

```yaml
action: holabrain.refresh_token
data:
  config_entry_id: 01J…       # optional — limits it to one account
```

> ⚠️ **This signs the HolaBrain mobile app out.** A login claims the account's single
> session. The same action is available as the **Refresh token** button on the account
> device, which is disabled by default so it cannot be pressed by accident.
````

- [ ] **Step 5: Note the consequence in `docs/accounts.md`**

Add a row to the "The short version" table, directly after the `**Refresh now** (account device)` row:

```markdown
| **Refresh token** (account device, `holabrain.refresh_token`) | **Yes** — a login claims the session |
```

- [ ] **Step 6: Run the full gate**

Run:

```bash
ruff check custom_components tests scripts
pytest tests/aiodollin/test_no_ha_imports.py --no-cov
pytest
python scripts/check_translations.py
```

Expected: all four PASS.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml custom_components/holabrain/manifest.json \
        CHANGELOG.md README.md docs/accounts.md
git commit -m "feat: manual account token refresh (0.15.0)"
```

---

## Notes for the implementer

- **`_async_recover` keeps its job.** It handles auth failures whose cause is not identified. Do not fold it into `_async_relogin`; the cool-down it applies is the only thing preventing an infinite login war with the vendor's mobile app.
- **The code-to-meaning mapping is reconstructed from test fixtures**, not vendor documentation. If field reports show frequent re-logins, `_TOKEN_EXPIRED_CODES` is the first thing to re-examine — `14005` may turn out to mean "claimed elsewhere", in which case it belongs back in the unknown set.
- **Unrecognised auth codes deliberately keep today's behaviour.** Do not widen `_TOKEN_EXPIRED_CODES` to cover a code you have not seen in a real response.
- **Cooperative mode is not weakened by any of this.** A silent re-login only ever happens inside a request that was already being made; the manual refresh is a direct user instruction and is allowed in both modes.
