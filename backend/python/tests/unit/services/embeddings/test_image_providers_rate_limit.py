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


def _config(provider: str, **extra: Any) -> MultimodalProviderConfig:
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
        def __init__(self, **_: Any) -> None:
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
