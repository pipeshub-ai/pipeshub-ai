"""Where a LangChain model keeps the httpx clients it sends requests through.

One table, used by both the egress guard (``aimodels._guard_clients``) and
identity forwarding, so a provider added to one is covered by the other.

Each path leads to an SDK client (``openai.OpenAI``, ``anthropic.Anthropic``,
``ollama.Client`` or an async twin); every one of those holds its httpx client
in ``_client``.
"""

from __future__ import annotations

from dataclasses import dataclass
from operator import attrgetter
from typing import Any

import httpx

OPENAI_ROOT_CLIENTS = ("root_client", "root_async_client")
OPENAI_RESOURCE_CLIENTS = ("client._client", "async_client._client")
OWN_CLIENTS = ("_client", "_async_client")
ALL_CLIENT_PATHS = OPENAI_ROOT_CLIENTS + OPENAI_RESOURCE_CLIENTS + OWN_CLIENTS

# Models that hold the httpx client itself (ChatMistralAI, or any LangChain model
# built with ``http_client=``) rather than an SDK client around it.
DIRECT_HTTPX_ATTRS = ("client", "async_client", "http_client", "http_async_client")

HttpxClient = (httpx.Client, httpx.AsyncClient)


@dataclass(frozen=True)
class ClientSlot:
    """An httpx client and the object attribute that holds it, so it can be replaced."""

    owner: Any
    attr: str
    client: Any

    def replace(self, new_client: Any) -> None:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
        setattr(self.owner, self.attr, new_client)


def _resolve(model: Any, path: str) -> Any | None:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    try:
        return attrgetter(path)(model)
    except Exception:
        # A path the model does not have (ChatOllama has no root_client), or a
        # lazily built client that fails to build, is simply not a candidate.
        return None


def sdk_http_client(sdk_client: Any) -> Any | None:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    inner = getattr(sdk_client, "_client", None)
    return inner if isinstance(inner, HttpxClient) else None


def iter_client_slots(model: Any, paths: tuple[str, ...] = ALL_CLIENT_PATHS) -> list[ClientSlot]:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """Every place ``model`` holds an httpx client. One client can appear in several slots."""
    slots: list[ClientSlot] = []
    seen = set()

    def add(owner: Any, attr: str, client: Any) -> None:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
        key = (id(owner), attr)
        if key not in seen:
            seen.add(key)
            slots.append(ClientSlot(owner, attr, client))

    for attr in DIRECT_HTTPX_ATTRS:
        direct = _resolve(model, attr)
        if isinstance(direct, HttpxClient):
            add(model, attr, direct)
    for path in paths:
        sdk_client = _resolve(model, path)
        if sdk_client is not None and sdk_http_client(sdk_client) is not None:
            add(sdk_client, "_client", sdk_client._client)
    return slots


def iter_http_clients(model: Any, paths: tuple[str, ...] = ALL_CLIENT_PATHS) -> list[Any]:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    """Every distinct httpx client ``model`` sends requests through."""
    found: list[Any] = []
    seen = set()
    for slot in iter_client_slots(model, paths):
        if id(slot.client) not in seen:
            seen.add(id(slot.client))
            found.append(slot.client)
    return found
