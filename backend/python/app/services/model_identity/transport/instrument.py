"""Installs identity forwarding on the clients of a model.

Called from the two funnels every model passes through, ``get_generator_model``
and ``get_embedding_model`` (via ``_decorate_*``), and from the media-client
factory. Nothing here changes behaviour for a connection that has not enabled
forwarding.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import httpx

from app.services.model_identity import metrics
from app.services.model_identity.forwarders.registry import get_forwarder_registry
from app.services.model_identity.runtime import note_forwarding
from app.services.model_identity.settings import ConnectionForwardingSettings
from app.services.model_identity.transport.client_paths import (
    iter_client_slots,
    iter_http_clients,
)
from app.services.model_identity.transport.httpx_hook import (
    has_identity_hook,
    install_identity_hook,
)
from app.services.model_identity.transport.private_client import private_copy

logger = logging.getLogger(__name__)

CONFIG_KEY = "identityForwarding"


def settings_from_config(config: Mapping[str, Any] | None) -> ConnectionForwardingSettings:
    """``configuration.identityForwarding`` of a model entry, or the disabled default."""
    configuration = (config or {}).get("configuration") or {}
    if not isinstance(configuration, Mapping):
        return ConnectionForwardingSettings()
    nested = configuration.get(CONFIG_KEY)
    if nested is not None:
        return ConnectionForwardingSettings.parse(nested)
    return ConnectionForwardingSettings.parse(_flat_settings(configuration))


# The admin dialog renders the registry's flat fields; the same settings may also
# arrive as one nested object through the API. The nested form wins when present.
_FLAT_KEYS = {
    "identityForwardingEnabled": "enabled",
    "identityForwardingMode": "mode",
    "identityForwardingPreset": "preset",
    "identityForwardingServiceApiKey": "serviceApiKey",
    "identityForwardingHeaderTemplates": "headerTemplates",
    "identityForwardingJwtAudience": "jwtAudience",
    "identityForwardingServiceAuth": "serviceAuth",
}


def _flat_settings(configuration: Mapping[str, Any]) -> dict[str, Any] | None:
    found = {target: configuration[key] for key, target in _FLAT_KEYS.items() if key in configuration}
    if not found:
        return None
    if found.get("serviceApiKey") == "":
        found.pop("serviceApiKey")
    return found


def instrument_http_client(client: Any, settings: ConnectionForwardingSettings) -> bool:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """Install the hook on one httpx client. False when forwarding is off or unavailable."""
    if not settings.enabled:
        return False
    forwarder = get_forwarder_registry().get(settings)
    if forwarder is None:
        logger.warning(
            "identity forwarding mode %r is not available in this edition; no identity is sent",
            settings.mode,
        )
        return False
    return install_identity_hook(client, settings, forwarder)


def _endpoint_of(config: Mapping[str, Any] | None) -> str | None:
    configuration = (config or {}).get("configuration")
    endpoint = configuration.get("endpoint") if isinstance(configuration, Mapping) else None
    return endpoint if isinstance(endpoint, str) and endpoint.strip() else None


def instrument_model(model: Any, provider: str, config: Mapping[str, Any] | None) -> Any:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """Install identity forwarding on every httpx client of a chat or embeddings model.

    Idempotent, and a no-op when the connection has not enabled forwarding.
    Returns ``model`` so it can sit in a decorator chain.

    Each client is replaced by a private copy first (see ``private_client``): the
    SDKs share one default client between models with the same base URL, and the
    hook is per connection.
    """
    settings = settings_from_config(config)
    if not settings.enabled:
        return model
    forwarder = get_forwarder_registry().get(settings)
    if forwarder is None:
        logger.warning(
            "identity forwarding mode %r is not available in this edition; no identity is sent",
            settings.mode,
        )
        return model
    note_forwarding(requires_groups=forwarder.requires_groups(settings))
    slots = iter_client_slots(model)
    if not slots:
        # A provider that does not speak httpx (Bedrock's boto, Vertex's gRPC)
        # cannot carry these headers; say so instead of silently sending none.
        logger.warning(
            "identity forwarding is enabled for %s but its client has no HTTP transport to attach it to",
            provider,
        )
        metrics.inc(metrics.IDENTITY_FORWARDED, settings.mode, "-", "no_client")
        return model

    endpoint = _endpoint_of(config)
    replacements: dict[int, Any] = {}
    for slot in slots:
        old = slot.client
        if has_identity_hook(old):
            continue
        client = replacements.get(id(old))
        if client is None:
            client = private_copy(old, endpoint)
            install_identity_hook(client, settings, forwarder)
            replacements[id(old)] = client
        if client is not old:
            slot.replace(client)
    return model


def model_http_client(
    base_url: str, config: Mapping[str, Any] | None = None
) -> httpx.AsyncClient:
    """A guarded async httpx client for media models, with identity forwarding when enabled."""
    from openai import DefaultAsyncHttpxClient

    from app.utils.model_egress import guard_httpx_client

    client = DefaultAsyncHttpxClient()
    guard_httpx_client(client, base_url)
    instrument_http_client(client, settings_from_config(config))
    return client


def identity_http_clients(model: Any) -> list[Any]:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """The httpx clients of ``model`` that carry the identity hook."""
    return [c for c in iter_http_clients(model) if has_identity_hook(c)]


def identity_async_http_client(model: Any) -> httpx.AsyncClient | None:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """The model's instrumented async client, for a direct transport to reuse.

    A direct SDK transport builds its own client, so without this its requests
    would carry no identity. Reusing the LangChain model's client gives the two
    paths the same egress guard, the same hook and one connection pool.
    """
    for client in identity_http_clients(model):
        if isinstance(client, httpx.AsyncClient):
            return client
    return None
