"""Single-session behaviour: sharing one account slot with the vendor's mobile app.

The cloud keeps exactly one live session per account — logging in invalidates the previous
token, which then fails every request. Home Assistant therefore shares that slot with the
phone app, and the naive reaction (log in again immediately) turns into a ping-pong: each
side keeps logging the other one out, forever, at one login per poll.

These tests drive a fake clock so the cool-down can be observed without waiting.
"""

import contextlib

import pytest

from custom_components.holabrain.aiodollin.auth.manager import (
    EVICTION_FORGET_SECONDS,
    LOGIN_BACKOFF_SECONDS,
    AuthManager,
)
from custom_components.holabrain.aiodollin.auth.store import InMemoryTokenStore, Session
from custom_components.holabrain.aiodollin.exceptions import AuthError, TokenExpiredError


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class Cloud:
    """A cloud that allows one live token and evicts the previous one on every login."""

    def __init__(self) -> None:
        self.live: str | None = None
        self.logins = 0
        self.calls = 0
        self._expiring = 0

    def login(self) -> str:
        self.logins += 1
        self.live = f"TOKEN-{self.logins}"
        return self.live

    def use(self, token: str) -> None:
        self.calls += 1
        if self._expiring:
            # An ordinary expiry, not a takeover: the token used to be valid.
            self._expiring -= 1
            raise TokenExpiredError("token invalid")
        if token != self.live:
            raise AuthError("We've detected unusual activity on your account")

    def other_client_logs_in(self) -> None:
        """The phone app takes the session over."""
        self.login()

    def expire_the_current_token(self) -> None:
        """The live token reaches the natural end of its life; the next request rejects it."""
        self._expiring += 1


class FakeTransport:
    region = "eu"

    def __init__(self, cloud: Cloud) -> None:
        self._cloud = cloud

    async def oem_request(self, path, payload=None, *, access_token=""):
        if path.endswith("/user/login/new"):
            return {"code": 0, "data": {"accessToken": self._cloud.login(), "uid": "1"}}
        self._cloud.use(access_token)
        return {"code": 0, "data": {}}

    async def tob_request(self, path, payload=None, *, access_token=""):
        self._cloud.use(access_token)
        return {"code": 0, "data": {}}


def _manager(cloud: Cloud, clock: FakeClock, store=None) -> AuthManager:
    return AuthManager(
        FakeTransport(cloud),
        store if store is not None else InMemoryTokenStore(),
        account="user@example.com",
        password="pw",
        clock=clock,
    )


@pytest.mark.asyncio
async def test_a_stored_session_is_reused_instead_of_logging_in():
    """A restart must not evict the phone app. This is the whole point of persisting."""
    cloud = Cloud()
    cloud.live = "TOKEN-PRESET"
    manager = _manager(
        cloud, FakeClock(), store=InMemoryTokenStore(Session(access_token="TOKEN-PRESET"))
    )

    await manager.oem("/v1/thing")

    assert cloud.logins == 0  # never touched the account


@pytest.mark.asyncio
async def test_the_first_takeover_is_reclaimed_immediately():
    """A single eviction is usually just an expired session; recovering must be seamless."""
    cloud = Cloud()
    manager = _manager(cloud, FakeClock())
    await manager.oem("/v1/thing")

    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")  # must not raise

    assert cloud.logins == 3  # ours, the other client's, ours again


@pytest.mark.asyncio
async def test_repeated_takeovers_are_not_fought_over():
    """The second takeover in a row must back off instead of stealing the session back.

    Without this the integration and the phone app log each other out on every poll for as
    long as the user keeps the app open.
    """
    clock = FakeClock()
    cloud = Cloud()
    manager = _manager(cloud, clock)
    await manager.oem("/v1/thing")

    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")  # first takeover: reclaimed
    logins_after_reclaim = cloud.logins

    cloud.other_client_logs_in()
    with pytest.raises(AuthError, match="in use by another client"):
        await manager.oem("/v1/thing")

    assert cloud.logins == logins_after_reclaim + 1  # only the app logged in, not us


@pytest.mark.asyncio
async def test_the_session_is_reclaimed_once_the_cool_down_passes():
    """Backing off must not mean giving up: the integration recovers on a later cycle."""
    clock = FakeClock()
    cloud = Cloud()
    manager = _manager(cloud, clock)
    await manager.oem("/v1/thing")

    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")
    cloud.other_client_logs_in()
    with pytest.raises(AuthError):
        await manager.oem("/v1/thing")

    clock.advance(LOGIN_BACKOFF_SECONDS[1] + 1)
    await manager.oem("/v1/thing")  # recovered without user intervention


@pytest.mark.asyncio
async def test_the_cool_down_grows_while_the_other_client_keeps_winning():
    """Each further takeover waits longer, so a busy app is left alone entirely."""
    clock = FakeClock()
    cloud = Cloud()
    manager = _manager(cloud, clock)
    await manager.oem("/v1/thing")

    for step in range(1, 4):
        cloud.other_client_logs_in()
        with contextlib.suppress(AuthError):
            await manager.oem("/v1/thing")
        # Wait exactly the cool-down of this step, so the next reclaim is allowed.
        clock.advance(LOGIN_BACKOFF_SECONDS[min(step, len(LOGIN_BACKOFF_SECONDS) - 1)] + 1)

    assert manager.evictions >= 3  # the takeovers were counted, not forgotten


@pytest.mark.asyncio
async def test_an_isolated_takeover_much_later_does_not_inherit_the_backoff():
    """A takeover half an hour later is a new event, not a continuing fight.

    Otherwise one bad afternoon would leave the integration in a long cool-down for the rest
    of its life.
    """
    clock = FakeClock()
    cloud = Cloud()
    manager = _manager(cloud, clock)
    await manager.oem("/v1/thing")

    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")
    assert manager.evictions == 1

    clock.advance(EVICTION_FORGET_SECONDS + 1)
    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")  # immediate again, no cool-down inherited

    assert manager.evictions == 1


@pytest.mark.asyncio
async def test_a_rejected_token_is_dropped_from_storage():
    """Keeping an evicted token would make the next restart start from a dead session."""
    store = InMemoryTokenStore()
    cloud = Cloud()
    manager = _manager(cloud, FakeClock(), store=store)
    await manager.oem("/v1/thing")
    assert (await store.load()) is not None

    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")

    session = await store.load()
    assert session is not None and session.access_token == cloud.live


@pytest.mark.asyncio
async def test_polling_a_healthy_session_never_logs_in_again():
    """Steady state must cost zero logins — that is what keeps the app usable."""
    cloud = Cloud()
    manager = _manager(cloud, FakeClock())
    await manager.oem("/v1/thing")
    logins = cloud.logins

    for _ in range(20):
        await manager.oem("/v1/thing")

    assert cloud.logins == logins


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

    # Three ordinary expiries inside the forget window, driven through the public API.
    for _ in range(3):
        cloud.expire_the_current_token()
        await manager.oem("/v1/thing")

    assert manager.evictions == 0

    cloud.other_client_logs_in()
    await manager.oem("/v1/thing")  # first takeover: must still be reclaimed immediately
    assert manager.evictions == 1  # recorded once, not escalated by the earlier expiries
