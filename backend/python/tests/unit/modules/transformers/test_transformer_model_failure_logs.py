"""Indexing transformers that call a model (image descriptions, dense embeddings, entity embeddings) log its
failure without the provider's text, which can echo the image, the chunk or the entity names it was sent.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import openai
import pytest

import app.modules.transformers.vectorstore as vectorstore_module
from app.modules.transformers.image_description import ImageDescriber
from app.modules.transformers.vectorstore import VectorStore
from tests.support.provider_errors import (
    MARK,
    assert_text_free,
    failure_line,
    openai_error,
)
from tests.unit.modules.transformers.test_entity_vectorstore_write_outcome import (
    _DB,
    _store,
    _topic,
)


async def test_an_image_the_model_refuses_is_skipped_with_its_type_only(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    describer = ImageDescriber(logging.getLogger("test.images"), MagicMock())
    describer._describe_one = AsyncMock(side_effect=[openai_error(400), "A bar chart"])
    describer._write = MagicMock()

    written = await describer._describe_all([(SimpleNamespace(index=0), "u0"), (SimpleNamespace(index=1), "u1")], MagicMock())

    assert written == 1
    (note,) = [r for r in caplog.records if "Could not describe image block" in r.getMessage()]
    assert (note.levelno, note.error_type) == (logging.DEBUG, "BadRequestError")
    assert_text_free(caplog.records)


def _store_for_retry(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, *outcomes: object) -> SimpleNamespace:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(vectorstore_module, "retry_delay_seconds", lambda _attempt: 0.0)
    return SimpleNamespace(
        embedding_provider="openAI", logger=logging.getLogger("test.vectorstore"),
        dense_embeddings=MagicMock(aembed_documents=AsyncMock(side_effect=list(outcomes))),
        _is_local_cpu_embedding=lambda: False,
    )


async def test_a_retried_dense_embedding_names_the_type_only(caplog, monkeypatch) -> None:
    store = _store_for_retry(caplog, monkeypatch, openai_error(429), openai_error(429), [[0.1]])

    assert await VectorStore._embed_documents_with_retry(store, ["chunk"], "rec-1") == [[0.1]]

    notes = [r for r in caplog.records if "Dense embedding attempt" in r.getMessage()]
    assert [(r.levelno, r.error_type) for r in notes] == [(logging.WARNING, "RateLimitError")] * 2
    assert_text_free(caplog.records)


async def test_a_transport_failure_names_its_innermost_cause(caplog, monkeypatch) -> None:
    request = httpx.Request("POST", "https://api.example/v1/embeddings")
    error = openai.APIConnectionError(message=f"Connection error. {MARK}", request=request)
    error.__cause__ = httpx.ConnectError(MARK, request=request)
    store = _store_for_retry(caplog, monkeypatch, error, error, error)

    with pytest.raises(vectorstore_module.EmbeddingError):
        await VectorStore._embed_documents_with_retry(store, ["chunk"], "rec-1")

    notes = [r for r in caplog.records if "Dense embedding attempt" in r.getMessage()]
    assert [(r.error_type, r.cause_type) for r in notes] == [("APIConnectionError", "ConnectError")] * 2
    assert_text_free(caplog.records)


async def test_a_failed_entity_embedding_is_one_text_free_line(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    store = _store(_DB())
    store._dense_embeddings = MagicMock(embed_documents=MagicMock(side_effect=openai_error(401)))

    outcome = await store.upsert_entities_batch([_topic("a"), _topic("b")], merge_membership=False)

    assert outcome.failed == 2
    fields = failure_line(caplog.records, "entity_index.upsert_failed", "misconfigured", logging.ERROR)
    assert (fields["batch_start"], fields["count"]) == (0, 2)
    assert_text_free(caplog.records)


async def test_a_failed_entity_write_keeps_the_vector_store_class(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    db = _DB()
    db.fail_upserts = True
    store = _store(db)

    outcome = await store.upsert_entities_batch([_topic("a")], merge_membership=False)

    assert outcome.failed == 1
    failure_line(caplog.records, "entity_index.upsert_failed", "internal", logging.ERROR)
