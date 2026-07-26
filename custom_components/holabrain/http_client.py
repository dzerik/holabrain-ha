"""Construction of the HTTP client the cloud transport runs on.

``httpx.AsyncClient()`` builds its TLS context while it is constructed, and that reads the
CA bundle from disk. Doing so from the event loop is blocking I/O — Home Assistant detects
and reports it — and it would happen on every config-flow step, every setup and every
reload. Home Assistant keeps a pre-warmed, cached client context for exactly this reason, so
the client is built from that instead of letting httpx create its own.

The client is *owned by the caller*: unlike the shared Home Assistant client it carries no
Home Assistant user agent (the cloud is picky about the headers the transport sets itself)
and it must be closed when the config entry or the config flow is done with it.
"""

from __future__ import annotations

import httpx
from homeassistant.core import callback
from homeassistant.util.ssl import SSL_ALPN_HTTP11, SSLCipherList, client_context

# Keeping a couple of connections alive covers the poll cycle without holding sockets open
# indefinitely; the cloud closes idle connections on its own well before this.
_LIMITS = httpx.Limits(max_keepalive_connections=4, keepalive_expiry=15.0)


@callback
def async_create_http_client() -> httpx.AsyncClient:
    """Return a new HTTP client for the cloud transport. The caller must close it."""
    return httpx.AsyncClient(
        verify=client_context(SSLCipherList.PYTHON_DEFAULT, SSL_ALPN_HTTP11),
        limits=_LIMITS,
    )
