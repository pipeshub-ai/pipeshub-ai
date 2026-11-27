"""Make an OpenAI-protocol embeddings client decode its responses with ``embedding_response_decoder``.

LangChain's ``OpenAIEmbeddings`` calls ``client.create`` and ``async_client.create`` and reads either a model or a dict.
The adapter swaps those two calls on the instance for ones that send the same request through the SDK's raw-response
path (so headers, retries, timeouts, status errors and the HTTP client are the SDK's own) and decode the body here,
on a thread when it is large, instead of in the SDK's per-float model constructor on the event loop.
"""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar, cast

from langchain_openai.embeddings import OpenAIEmbeddings
from openai.resources.embeddings import AsyncEmbeddings, Embeddings

from app.utils.embedding_response_decoder import (
    OFF_LOOP_BODY_BYTES,
    DecodedEmbeddings,
    decode_embedding_response,
)

_Model = TypeVar("_Model")


def _expected_count(kwargs: dict[str, Any]) -> int | None:
    """How many vectors a request asks for: a string is one input, a list holds one input per item."""
    inputs: object = kwargs.get("input")
    if isinstance(inputs, str):
        return 1
    return len(cast("list[Any]", inputs)) if isinstance(inputs, list) and inputs else None


def _decode(body: bytes, expected: int | None) -> dict[str, Any]:
    decoded: DecodedEmbeddings = decode_embedding_response(body, expected_count=expected)
    return decoded.as_response()


def _decoding_create(resource: Embeddings) -> Any:  # noqa: ANN401 - replaces a method of an SDK resource
    send = resource.with_raw_response.create

    def create(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401 - the SDK's own keyword arguments
        raw = send(**kwargs)
        return _decode(raw.http_response.content, _expected_count(kwargs))

    return create


def _async_decoding_create(resource: AsyncEmbeddings, off_loop_bytes: int) -> Any:  # noqa: ANN401 - replaces a method of an SDK resource
    send = resource.with_raw_response.create

    async def create(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401 - the SDK's own keyword arguments
        raw = await send(**kwargs)
        body = raw.http_response.content
        expected = _expected_count(kwargs)
        if len(body) < off_loop_bytes:
            return _decode(body, expected)
        return await asyncio.to_thread(_decode, body, expected)

    return create


def decode_embedding_responses(model: _Model, *, off_loop_bytes: int = OFF_LOOP_BODY_BYTES) -> _Model:
    """Install the decoding ``create`` on the ``client`` and ``async_client`` of an OpenAI-protocol embeddings model.

    Idempotent. Only LangChain's ``OpenAIEmbeddings`` (and its Azure subclass) is adapted, since only it reads the
    dict a decoded response is returned as; any other model, and a client the model does not have (an unavailable
    sync client), is left as it is. On the async path a body of ``off_loop_bytes`` or more is decoded on a thread, a
    smaller one inline (the hop would cost more than the decode).
    """
    if not isinstance(model, OpenAIEmbeddings):
        return model
    client = getattr(model, "client", None)
    if isinstance(client, Embeddings) and "create" not in vars(client):
        client.create = _decoding_create(client)  # type: ignore[method-assign]
    async_client = getattr(model, "async_client", None)
    if isinstance(async_client, AsyncEmbeddings) and "create" not in vars(async_client):
        async_client.create = _async_decoding_create(async_client, off_loop_bytes)  # type: ignore[method-assign]
    return model
