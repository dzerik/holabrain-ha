"""Token storage abstraction.

The core never decides *where* a session lives — Home Assistant persists it in the config
entry, a CLI would use a JSON file, tests use the in-memory store. This keeps the auth flow
side-effect free and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Session:
    """A minted session (account token plus the context needed to reuse it)."""

    access_token: str
    account: str = ""
    region: str = "eu"
    uid: str = ""


@runtime_checkable
class TokenStore(Protocol):
    """Persistence contract for a :class:`Session`."""

    async def load(self) -> Session | None: ...

    async def save(self, session: Session) -> None: ...

    async def clear(self) -> None: ...


class InMemoryTokenStore:
    """A :class:`TokenStore` that keeps the session in memory (tests, ephemeral clients)."""

    def __init__(self, session: Session | None = None) -> None:
        self._session = session

    async def load(self) -> Session | None:
        return self._session

    async def save(self, session: Session) -> None:
        self._session = session

    async def clear(self) -> None:
        self._session = None
