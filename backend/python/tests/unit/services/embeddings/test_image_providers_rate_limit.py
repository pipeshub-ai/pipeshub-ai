"""A provider's wait past the call's allowance reaches the record handler from the image embedding providers.

``EmbeddingGateway.call`` raises ``EmbeddingRateLimited``; the image providers turn an ordinary failure into a per-image error
result, but this one is the whole record's to wait for, so it must leave them (the record is handed back instead of finishing
without its image vectors).
"""

from __future__ import annotations

import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.embeddings.multimodal.config import MultimodalProviderConfig
from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory
from app.services.llm_gateway.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingRateLimited,
)
from app.services.spend_guard.retry_policy import RetryBudget

IMAGE = "data:image/png;base64,AAAA"


class Throttle(Exception):
    """A provider's 429 with a Retry-After longer than a call may wait."""

    status_code = 429

    class response:
        headers = {"retry-after": "600"}


def _config(provider: str, **extra: Any) -> MultimodalProviderConfig:  # noqa: ANN401
    return MultimodalProviderConfig(
        provider=provider, api_key="k", model_name="m", region_name="us-east-1", aws_access_key_id="a",
        aws_secret_access_key="s", sdk_retries=0, normalize_fn=lambda ref: ref, **extra,
    )


@pytest.fixture
def one_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    gateway = EmbeddingGateway(retry_budget=RetryBudget(), max_attempts=1)
    for name in ("cohere", "gemini", "bedrock", "voyage"):
        monkeypatch.setattr(f"app.services.embeddings.multimodal.{name}_provider.get_embedding_gateway", lambda: gateway)


async def test_cohere_hands_the_wait_over(monkeypatch: pytest.MonkeyPatch, one_attempt: None) -> None:
    client = MagicMock()
    client.embed.side_effect = Throttle("slow")
    monkeypatch.setitem(sys.modules, "cohere", types.SimpleNamespace(ClientV2=lambda api_key: client))
    provider = MultimodalEmbeddingFactory.create(_config("cohere"))
    with pytest.raises(EmbeddingRateLimited) as raised:
        await provider.embed_images([IMAGE])  # type: ignore[union-attr]
    assert raised.value.retry_after == 600.0


async def test_voyage_hands_the_wait_over(one_attempt: None) -> None:
    embeddings = MagicMock()
    embeddings.aembed_documents = AsyncMock(side_effect=Throttle("slow"))
    provider = MultimodalEmbeddingFactory.create(_config("voyage", dense_embeddings=embeddings))
    with pytest.raises(EmbeddingRateLimited):
        await provider.embed_images([IMAGE])  # type: ignore[union-attr]


async def test_gemini_hands_the_wait_over(monkeypatch: pytest.MonkeyPatch, one_attempt: None) -> None:
    from google import genai

    class FakeClient:
        def __init__(self, **_: Any) -> None:  # noqa: ANN401
            self.aio = MagicMock()
            self.aio.models.embed_content = AsyncMock(side_effect=Throttle("slow"))

    monkeypatch.setattr(genai, "Client", FakeClient)
    provider = MultimodalEmbeddingFactory.create(_config("gemini"))
    with pytest.raises(EmbeddingRateLimited):
        await provider.embed_images(["AAAA"])  # type: ignore[union-attr]  # raw base64: Gemini decodes it itself


async def test_bedrock_hands_the_wait_over(monkeypatch: pytest.MonkeyPatch, one_attempt: None) -> None:
    class BotoThrottle(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 429, "HTTPHeaders": {"retry-after": "600"}}}

    client = MagicMock()
    client.invoke_model.side_effect = BotoThrottle("ThrottlingException")
    monkeypatch.setattr("boto3.client", lambda **_: client)
    provider = MultimodalEmbeddingFactory.create(_config("bedrock"))
    with pytest.raises(EmbeddingRateLimited) as raised:
        await provider.embed_images([IMAGE])  # type: ignore[union-attr]
    assert raised.value.retry_after == 600.0


async def test_gemini_images_are_sent_through_httpx_when_the_sdk_must_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """With aiohttp installed google-genai repeats a request after a lost reply on its own; through httpx it does not."""
    from app.services.llm_gateway import wire

    routed: list[object] = []
    monkeypatch.setattr(wire, "route_genai_through_httpx", lambda client: routed.append(client) or 1)
    provider = MultimodalEmbeddingFactory.create(_config("gemini"))
    await provider.embed_images([])  # type: ignore[union-attr]
    assert len(routed) == 1


async def test_a_deferral_leaves_no_sibling_image_running(monkeypatch: pytest.MonkeyPatch, one_attempt: None) -> None:
    """With more images than the concurrency limit, the first image's wait must not leave the others to start paid requests after
    the caller has the deferral."""
    started: list[int] = []
    client = MagicMock()

    def embed(**_: Any) -> Any:  # noqa: ANN401
        started.append(1)
        raise Throttle("slow")

    client.embed.side_effect = embed
    monkeypatch.setitem(sys.modules, "cohere", types.SimpleNamespace(ClientV2=lambda api_key: client))
    provider = MultimodalEmbeddingFactory.create(_config("cohere"))
    with pytest.raises(EmbeddingRateLimited):
        await provider.embed_images([IMAGE] * 40)  # type: ignore[union-attr]
    count = len(started)
    import asyncio

    await asyncio.sleep(0.2)
    assert len(started) == count, "an image started a request after the deferral had been raised"
    assert count < 40


async def test_native_image_clients_count_their_requests_in_the_wire_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed image request is a send of the stage job: the Bedrock client carries wire hooks."""
    from app.services.llm_gateway import wire

    installed: list[object] = []
    monkeypatch.setattr(wire, "install_boto_hooks", lambda client: installed.append(client) or True)
    client = MagicMock()
    monkeypatch.setattr("boto3.client", lambda **_: client)
    provider = MultimodalEmbeddingFactory.create(_config("bedrock"))
    await provider.embed_images([])  # type: ignore[union-attr]
    assert installed == [client]


async def test_bedrock_deferral_leaves_no_sibling_image_running(monkeypatch: pytest.MonkeyPatch, one_attempt: None) -> None:
    """The same for Bedrock: images queued behind the semaphore must not start a paid request after the deferral."""
    import asyncio

    class BotoThrottle(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 429, "HTTPHeaders": {"retry-after": "600"}}}

    started: list[int] = []
    client = MagicMock()

    def invoke(**_: Any) -> Any:  # noqa: ANN401
        started.append(1)
        raise BotoThrottle("ThrottlingException")

    client.invoke_model.side_effect = invoke
    monkeypatch.setattr("boto3.client", lambda **_: client)
    provider = MultimodalEmbeddingFactory.create(_config("bedrock"))
    with pytest.raises(EmbeddingRateLimited):
        await provider.embed_images([IMAGE] * 40)  # type: ignore[union-attr]
    count = len(started)
    await asyncio.sleep(0.2)
    assert len(started) == count and count < 40


async def test_bedrock_one_images_unexpected_failure_does_not_abort_the_batch(
    monkeypatch: pytest.MonkeyPatch, one_attempt: None
) -> None:
    client = MagicMock()
    client.invoke_model.side_effect = ValueError("a malformed image")
    monkeypatch.setattr("boto3.client", lambda **_: client)
    provider = MultimodalEmbeddingFactory.create(_config("bedrock"))
    results = await provider.embed_images([IMAGE, IMAGE])  # type: ignore[union-attr]
    assert [r.error is not None for r in results] == [True, True]


async def test_voyage_repeats_a_transport_failure_through_the_real_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual path: ``VoyageEmbeddings.aembed_documents`` posts with ``requests``; a refused connection is retried by the
    gateway (three sends), and is not turned into a per-image error after one."""
    import requests

    from app.services.llm_gateway import embedding_gateway as gateway_module
    from app.utils.custom_embeddings import VoyageEmbeddings

    posts: list[int] = []

    def post(**_: Any) -> Any:  # noqa: ANN401
        posts.append(1)
        raise requests.exceptions.ConnectionError("refused")

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.utils.custom_embeddings.requests.post", post)
    monkeypatch.setattr(gateway_module.asyncio, "sleep", instant)
    gateway = EmbeddingGateway(retry_budget=RetryBudget())
    monkeypatch.setattr("app.services.embeddings.multimodal.voyage_provider.get_embedding_gateway", lambda: gateway)
    embeddings = VoyageEmbeddings(model="voyage-multimodal-3", voyage_api_key="k", max_retries=1)
    provider = MultimodalEmbeddingFactory.create(_config("voyage", dense_embeddings=embeddings))
    results = await provider.embed_images([IMAGE])  # type: ignore[union-attr]
    assert len(posts) == 3 and results[0].error is not None
