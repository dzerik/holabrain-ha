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


class NetworkError(DollinError):
    """The request could not be completed due to a transport/connectivity problem."""


class ApiError(DollinError):
    """The cloud accepted the request but returned a non-success business code."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class RateLimitError(ApiError):
    """The cloud rejected the request because of rate limiting / throttling."""
