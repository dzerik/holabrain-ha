"""Exception hierarchy for the aiodollin cloud client.

The Home Assistant adapter maps these onto HA errors in exactly one place, so the rest of
the codebase only ever deals with this small, transport-agnostic set.
"""

from __future__ import annotations


class DollinError(Exception):
    """Base class for every error raised by aiodollin."""


class AuthError(DollinError):
    """Authentication or authorization failed (bad credentials, expired token)."""


class SessionTakeoverError(AuthError):
    """Another client is holding the account's single session slot.

    Distinct from a plain :class:`AuthError` because the credentials are fine: the session
    was simply claimed by the vendor's mobile app and reclaiming it is deliberately delayed.
    Callers must treat this as a temporary condition and must **not** ask the user to sign
    in again.
    """


class NetworkError(DollinError):
    """The request could not be completed due to a transport/connectivity problem."""


class ApiError(DollinError):
    """The cloud accepted the request but returned a non-success business code."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class RateLimitError(ApiError):
    """The cloud rejected the request because of rate limiting / throttling."""
