"""A private copy of an SDK's default httpx client.

LangChain's OpenAI integration (and the SDKs under it) cache one default httpx
client per base URL and timeout, so two model entries pointing at the same
gateway share a client. A request hook installed on that client would apply to
both entries, whatever either one's settings say: forwarding enabled on one
connection would leak identity to another that has it off.

So a connection with forwarding enabled gets its own client. Only a client that
is plain default configuration is copied: one built with a custom transport, TLS,
proxy mounts, HTTP/2 or limits was supplied by someone for this model alone, and
copying it would silently drop those settings, so it is instrumented where it is.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# What the OpenAI and Anthropic SDKs use for a client they build themselves.
_DEFAULT_LIMITS = httpx.Limits(max_connections=1000, max_keepalive_connections=100)


def _uses_standard_transport(client: Any) -> bool:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    return isinstance(
        getattr(client, "_transport", None), (httpx.HTTPTransport, httpx.AsyncHTTPTransport)
    )


def _pool_of(client: Any) -> Any:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    return getattr(getattr(client, "_transport", None), "_pool", None)


def _limits_of(client: Any) -> httpx.Limits:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """The connection limits ``client`` was built with, so the copy keeps them."""
    pool = _pool_of(client)
    max_connections = getattr(pool, "_max_connections", None)
    max_keepalive = getattr(pool, "_max_keepalive_connections", None)
    if not isinstance(max_connections, int) or not isinstance(max_keepalive, int):
        return _DEFAULT_LIMITS
    return httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive,
        keepalive_expiry=getattr(pool, "_keepalive_expiry", None),
    )


def _transport_settings(client: Any) -> tuple[Any, ...]:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """What an httpx client's transport was built with that a copy cannot reproduce."""
    pool = _pool_of(client)
    ssl_context = getattr(pool, "_ssl_context", None)
    return (
        len(getattr(client, "_mounts", {}) or {}),
        getattr(pool, "_http2", None),
        getattr(ssl_context, "verify_mode", None),
        getattr(ssl_context, "check_hostname", None),
    )


def _is_egress_guarded(client: Any) -> bool:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    from app.utils.model_egress import VettedAsyncBackend, VettedSyncBackend

    pool = getattr(getattr(client, "_transport", None), "_pool", None)
    return isinstance(
        getattr(pool, "_network_backend", None), (VettedAsyncBackend, VettedSyncBackend)
    )


def private_copy(client: Any, endpoint: str | None) -> Any:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """A new client configured like ``client``, or ``client`` itself when it cannot be copied.

    The copy keeps the egress guard if the original had one, using ``endpoint``
    to decide whether private addresses are allowed, exactly as when it was
    first guarded.
    """
    if not _uses_standard_transport(client):
        return client
    kwargs = {
        "base_url": client.base_url,
        "headers": client.headers,
        "auth": client.auth,
        "params": client.params,
        "timeout": client.timeout,
        "follow_redirects": client.follow_redirects,
        "max_redirects": client.max_redirects,
        "trust_env": client.trust_env,
        "limits": _limits_of(client),
        "http2": bool(getattr(_pool_of(client), "_http2", False)),
        "event_hooks": {name: list(hooks) for name, hooks in client.event_hooks.items()},
    }
    try:
        copy = type(client)(**kwargs)
    except Exception:
        base = httpx.AsyncClient if isinstance(client, httpx.AsyncClient) else httpx.Client
        try:
            copy = base(**kwargs)
        except Exception:
            logger.warning("could not copy the HTTP client; instrumenting the shared one", exc_info=True)
            return client
    if _transport_settings(copy) != _transport_settings(client):
        logger.info("the HTTP client has custom transport settings; instrumenting it in place")
        return client
    if _is_egress_guarded(client):
        from app.utils.model_egress import guard_httpx_client

        guard_httpx_client(copy, endpoint)
    return copy
