"""New text vectors carry the weight precision of the response that made them; image points are marked as such."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.documents import Document

from app.services.vector_db.collection_epoch import Fence  # noqa: E402
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from tests.unit.modules.transformers.test_vectorstore_point_ids import (
    _make_vectorstore,
    _meta,
)


class _Bundled(EmbeddingServerEmbeddings):
    """The bundled-server client type, with nothing behind it."""

    def __init__(self) -> None:  # no HTTP clients, no settings
        self.model = "m"


async def _upsert(embedder: object, witnessed: tuple[str, bool] | None) -> list:
    store = _make_vectorstore()
    store.dense_embeddings = embedder
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "r1"})

    async def embed(texts, record_id, witness_out=None):  # noqa: ANN202
        if witnessed is not None and witness_out is not None:
            witness_out.append(witnessed)
        return [[0.1, 0.2]]

    store._embed_documents_with_retry = embed
    store._compute_sparse_embeddings = AsyncMock(return_value=[None])
    store._guard = MagicMock()
    store._guard.capture = AsyncMock(return_value=Fence("records", "t0"))
    store._guard.confirm_after_write = AsyncMock()
    store._guard.verify_batch = AsyncMock()
    await store._embed_and_upsert_documents(
        [Document(page_content="alpha", metadata=_meta())], "r1", "records",
    )
    store.verified = store._guard.verify_batch
    return store.vector_db_service.upsert_points.await_args.kwargs["points"], store


@pytest.mark.asyncio
async def test_every_point_carries_the_precision_its_batch_was_made_with() -> None:
    for precision in ("int8-w", "fp32"):  # fp32 is tagged too: an untagged point could not be told from a leak
        (point,), _ = await _upsert(_Bundled(), (precision, False))
        assert point.payload["metadata"]["embedPrecision"] == precision
        assert point.payload["page_content"] == "alpha"


@pytest.mark.asyncio
async def test_the_batch_is_checked_against_the_index_before_it_is_written() -> None:
    (_point,), store = await _upsert(_Bundled(), ("int8-w", True))
    store.verified.assert_awaited_once()
    args, kwargs = store.verified.await_args
    assert args[0] == "records" and args[2] == "int8-w" and kwargs["fallback"] is True


@pytest.mark.asyncio
async def test_a_refusal_stops_the_batch_before_the_upsert() -> None:
    from app.services.messaging.precision_flip import PrecisionFlip

    store = _make_vectorstore()
    store.dense_embeddings = _Bundled()
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "r1"})

    async def embed(texts, record_id, witness_out=None):  # noqa: ANN202
        witness_out.append(("fp32", False))
        return [[0.1, 0.2]]

    store._embed_documents_with_retry = embed
    store._compute_sparse_embeddings = AsyncMock(return_value=[None])
    store._guard = MagicMock()
    store._guard.capture = AsyncMock(return_value=Fence("records", "t0"))
    store._guard.confirm_after_write = AsyncMock()
    store._guard.verify_batch = AsyncMock(side_effect=PrecisionFlip("index holds int8-w"))
    with pytest.raises(PrecisionFlip):
        await store._embed_and_upsert_documents([Document(page_content="a", metadata=_meta())], "r1", "records")
    store.vector_db_service.upsert_points.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_provider_that_does_not_say_adds_no_tag_and_is_not_checked() -> None:
    for embedder in (object(), MagicMock()):
        (point,), store = await _upsert(embedder, None)
        assert "embedPrecision" not in point.payload["metadata"]
        store.verified.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_resume_key_does_not_change_when_a_batch_has_been_witnessed() -> None:
    """The key is fixed for an attempt and its retries: a batch's precision is known only after the first one."""
    store = _make_vectorstore()
    before = store._embed_key()
    store.dense_embeddings = _Bundled()
    store.graph_provider.get_document = AsyncMock(return_value={"_key": "r1"})

    async def embed(texts, record_id, witness_out=None):  # noqa: ANN202
        witness_out.append(("int8-w", False))
        return [[0.1, 0.2]]

    store._embed_documents_with_retry = embed
    store._compute_sparse_embeddings = AsyncMock(return_value=[None])
    store._guard = MagicMock()
    store._guard.capture = AsyncMock(return_value=Fence("records", "t0"))
    store._guard.confirm_after_write = AsyncMock()
    store._guard.verify_batch = AsyncMock()
    await store._embed_and_upsert_documents([Document(page_content="alpha", metadata=_meta())], "r1", "records")
    assert store._embed_key() == before  # a retry of the same attempt is still resuming it
