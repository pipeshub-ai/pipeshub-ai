"""Images sent to a provider's own SDK or endpoint are announced as the model call they are (``llm.call.completed``),
once, with the token count the provider's response gave and never an image."""

from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.embeddings.multimodal.interface import (
    ImageEmbeddingResult,
    IMultimodalEmbeddingProvider,
)
from app.utils import model_call_events
from tests.support.platform_event_capture import captured_events, facts
from tests.unit.modules.transformers.test_vectorstore import _make_vectorstore

IMAGE = "SEEDED-image-bytes"


class _Provider(IMultimodalEmbeddingProvider):
    def __init__(self, *, announces: bool = False, error: Exception | None = None) -> None:
        self._announces, self._error = announces, error

    @property
    def announces_model_calls(self) -> bool:
        return self._announces

    @property
    def provider_name(self) -> str:
        return "fake"

    async def embed_images(self, image_base64s: list[str]) -> list[ImageEmbeddingResult]:
        if self._error:
            raise self._error
        model_call_events.note_usage({"usage": {"total_tokens": 9}})
        return [ImageEmbeddingResult(index=i, embedding=[0.0]) for i in range(len(image_base64s))]


async def _embed(provider: _Provider) -> list[dict]:
    store = _make_vectorstore()
    store.embedding_provider, store.model_name = "jina", "jina-clip-v2"
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "r"})
    store._multimodal_provider_config = MagicMock()
    store._build_image_points = MagicMock(return_value=[])
    with captured_events("llm.call.completed") as events, patch(
        "app.modules.transformers.vectorstore.MultimodalEmbeddingFactory.create", return_value=provider,
    ):
        with contextlib.suppress(ConnectionError):
            await store._process_image_embeddings([{}, {}], [IMAGE, IMAGE], "r")
    return facts(events, "llm.call.completed")


async def test_images_sent_past_the_embeddings_client_are_announced_once_with_their_tokens() -> None:
    (call,) = await _embed(_Provider())

    assert (call["provider"], call["model"], call["operation"], call["count"], call["tokens_in"], call["outcome"]) == (
        "jina", "jina-clip-v2", "embedding", 2, 9, "success")
    assert IMAGE not in repr(call)


async def test_a_failed_send_is_announced_as_a_failure() -> None:
    (call,) = await _embed(_Provider(error=ConnectionError(IMAGE)))

    assert (call["outcome"], call["tokens_in"]) == ("failure", None)
    assert IMAGE not in repr(call)


async def test_a_provider_that_goes_through_the_announced_client_is_not_announced_a_second_time() -> None:
    assert await _embed(_Provider(announces=True)) == []


def test_only_the_provider_that_uses_the_embeddings_client_says_so() -> None:
    from app.services.embeddings.multimodal.jina_provider import JinaMultimodalProvider
    from app.services.embeddings.multimodal.voyage_provider import (
        VoyageMultimodalProvider,
    )

    assert VoyageMultimodalProvider.announces_model_calls.fget(object()) is True
    assert JinaMultimodalProvider.announces_model_calls.fget(object()) is False
