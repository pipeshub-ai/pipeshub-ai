"""What the sinks note for the record's index.end (docs/logging.md, "Indexing"): blocks, chunks and failed batches."""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document

from app.exceptions.indexing_exceptions import RecordDeletedError, VectorStoreError
from app.utils import indexing_timing as timing
from tests.unit.modules.transformers.test_vectorstore_coverage import _make_vectorstore


def _noted(run) -> dict:
    """Run ``run`` (a coroutine factory) inside a record timeline; return what was noted."""
    async def go() -> dict:
        timeline, token = timing.begin_record("r1")
        try:
            with contextlib.suppress(Exception):
                await run()
            return dict(timeline.facts)
        finally:
            timing.end_record(timeline, token, outcome="ok")
    return go()


async def test_the_sink_notes_the_block_count() -> None:
    from app.modules.transformers.sink_orchestrator import SinkOrchestrator

    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value=None)  # stops right after the note, as a deleted record
    sink = SinkOrchestrator(MagicMock(), MagicMock(), MagicMock(), graph, MagicMock(), AsyncMock())
    record = MagicMock(id="r1")
    record.block_containers.blocks = [object(), object(), object()]
    ctx = MagicMock(record=record, settings={})

    async def run() -> None:
        with pytest.raises(RecordDeletedError):
            await sink.index(ctx)

    assert (await _noted(run))["blocks"] == 3


async def test_concurrent_batches_note_how_many_failed() -> None:
    vs = _make_vectorstore()
    vs._is_local_cpu_embedding = MagicMock(return_value=False)
    vs._batches_by_tokens = MagicMock(return_value=False)
    calls = 0

    async def embed(batch, *_args) -> None:
        nonlocal calls
        calls += 1
        if calls != 1:
            raise ConnectionError("down")

    vs._embed_and_upsert_documents = embed
    docs = [Document(page_content="x" * 10, metadata={}) for _ in range(5)]

    async def run() -> None:
        with patch("app.modules.transformers.vectorstore._DEFAULT_DOCUMENT_BATCH_SIZE", 1), \
             pytest.raises(VectorStoreError):
            await vs._process_document_chunks(docs, "r1", "c")

    assert (await _noted(run))["failed_batches"] == 4


async def test_a_stored_record_notes_its_chunks() -> None:
    vs = _make_vectorstore()
    vs._process_document_chunks = AsyncMock()
    vs.delete_embeddings = AsyncMock()
    vs._store_image_points = AsyncMock()
    vs._process_image_embeddings = AsyncMock(return_value=[])
    chunks = [Document(page_content=f"c{i}", metadata={}) for i in range(3)]

    async def run() -> None:
        await vs._create_embeddings(chunks, "r1", "vr1", "c")

    noted = await _noted(run)
    assert noted.get("chunks") == 3, noted
