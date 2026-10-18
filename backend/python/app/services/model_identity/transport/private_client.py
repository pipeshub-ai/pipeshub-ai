"""A private copy of an SDK's default httpx client.

LangChain's OpenAI integration (and the SDKs under it) cache one default httpx
client per base URL and timeout, so two model entries pointing at the same
gateway share a client. A request hook installed on that client would apply to
both entries, whatever either one's settings say: forwarding enabled on one
connection would leak identity to another that has it off.

So a connection with forwarding enabled gets its own client. Only the standard
transport is copied: a client built with a custom transport was supplied by
someone for this model alone, and is instrumented where it is.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# What the OpenAI and Anthropic SDKs use for a client they build themselves.
_DEFAULT_LIMITS = httpx.Limits(max_connections=1000, max_keepalive_connections=100)


def _uses_standard_transport(client: Any) -> bool:
    return isinstance(
        getattr(client, "_transport", None), (httpx.HTTPTransport, httpx.AsyncHTTPTransport)
    )


def _is_egress_guarded(client: Any) -> bool:
    from app.utils.model_egress import VettedAsyncBackend, VettedSyncBackend

    pool = getattr(getattr(client, "_transport", None), "_pool", None)
    return isinstance(
        getattr(pool, "_network_backend", None), (VettedAsyncBackend, VettedSyncBackend)
    )


def private_copy(client: Any, endpoint: Optional[str]) -> Any:
    """A new client configured like ``client``, or ``client`` itself when it cannot be copied.

    The copy keeps the egress guard if the original had one, using ``endpoint``
    to decide whether private addresses are allowed, exactly as when it was
    first guarded.
    """
    if not _uses_standard_transport(client):
        return client
    kwargs = dict(
        base_url=client.base_url,
        headers=client.headers,
        auth=client.auth,
        params=client.params,
        timeout=client.timeout,
        follow_redirects=client.follow_redirects,
        max_redirects=client.max_redirects,
        trust_env=client.trust_env,
        limits=_DEFAULT_LIMITS,
        event_hooks={name: list(hooks) for name, hooks in client.event_hooks.items()},
    )
    try:
        copy = type(client)(**kwargs)
    except Exception:
        base = httpx.AsyncClient if isinstance(client, httpx.AsyncClient) else httpx.Client
        try:
            copy = base(**kwargs)
        except Exception:
            logger.warning("could not copy the HTTP client; instrumenting the shared one", exc_info=True)
            return client
    if _is_egress_guarded(client):
        from app.utils.model_egress import guard_httpx_client

        guard_httpx_client(copy, endpoint)
    return copy
