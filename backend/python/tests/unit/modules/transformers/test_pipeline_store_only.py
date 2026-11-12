"""``IndexingPipeline.apply`` in a store-only run (parse now, automatic indexing off): the content is
validated and stored, and nothing is purged, reconciled or handed to the pipeline stages."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.modules.indexing.parse_now_marker import ParseNowRun, begin_run, end_run
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import (
    ENRICHMENT_FOLLOWS,
    PARSE_NOW,
    STORE_ONLY,
)
from tests.unit.modules.transformers.test_pipeline import (
    _make_ctx,
    _make_record,
    _valid_text_block,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

REV = "rev-1"


@pytest.fixture
def doc_extraction() -> AsyncMock:
    extraction = AsyncMock()
    extraction.graph_provider = AsyncMock()
    extraction.graph_provider.update_node = AsyncMock(return_value=True)
    return extraction


@pytest.fixture
def sink_orchestrator() -> AsyncMock:
    sink = AsyncMock()
    sink.blob_storage = MagicMock()
    sink.vector_store = MagicMock()
    return sink


@pytest.fixture
def stage_ingress() -> MagicMock:
    ingress = MagicMock()
    ingress.on_indexed = AsyncMock(return_value=["vrid-1:rev:classify@1"])
    return ingress


@pytest.fixture
def pipeline(doc_extraction: AsyncMock, sink_orchestrator: AsyncMock, stage_ingress: MagicMock) -> IndexingPipeline:
    pipe = IndexingPipeline(doc_extraction, sink_orchestrator, stage_ingress=stage_ingress)
    pipe.logger = MagicMock()
    return pipe


@pytest.fixture(autouse=True)
def _run() -> Iterator[None]:
    begin_run(ParseNowRun(store_only=True, content_rev=REV))
    yield
    end_run()


def _store_only_ctx(record: MagicMock, **kwargs: object) -> MagicMock:
    ctx = _make_ctx(record, **kwargs)
    ctx.settings = {PARSE_NOW: True, STORE_ONLY: True}
    return ctx


@pytest.mark.asyncio
class TestStoreOnlyPipeline:
    async def test_stores_and_stops_before_the_stages(self, pipeline, sink_orchestrator, stage_ingress) -> None:
        # B5
        ctx = _store_only_ctx(_make_record(blocks=[_valid_text_block()], block_groups=[]))

        with patch.object(IndexingPipeline, "build_reconciliation_context", AsyncMock()) as reconcile:
            await pipeline.apply(ctx)

        sink_orchestrator.index.assert_awaited_once_with(ctx)
        stage_ingress.on_indexed.assert_not_awaited()
        reconcile.assert_not_awaited()
        assert ctx.reconciliation_context is None
        assert ENRICHMENT_FOLLOWS not in ctx.settings

    async def test_an_emptied_record_purges_nothing(self, pipeline, doc_extraction, sink_orchestrator) -> None:
        # A reindex of a 1:1 reconciled record would purge its vectors and reset its block list.
        record = _make_record(blocks=[], block_groups=[])
        ctx = _store_only_ctx(record, event_type=EventTypes.REINDEX_RECORD.value)
        ctx.prev_virtual_record_id = record.virtual_record_id
        sink_orchestrator.vector_store.purge_record_vectors = AsyncMock()
        sink_orchestrator.blob_storage.save_reconciliation_metadata = AsyncMock()

        await pipeline.apply(ctx)

        sink_orchestrator.vector_store.purge_record_vectors.assert_not_awaited()
        sink_orchestrator.blob_storage.save_reconciliation_metadata.assert_not_awaited()

    async def test_an_emptied_record_is_marked_not_given_a_status(self, pipeline, doc_extraction) -> None:
        ctx = _store_only_ctx(_make_record(blocks=[], block_groups=[], record_id="rec-1"))

        await pipeline.apply(ctx)

        fields = doc_extraction.graph_provider.update_node.await_args.args[2]
        assert fields == {"parseNowStatus": "EMPTY", "parseNowRev": REV, "parseNowReason": None, "parseNowAt": ANY}


@pytest.mark.asyncio
async def test_a_parse_now_run_that_indexes_adds_the_marker_to_the_status(pipeline, doc_extraction) -> None:
    end_run()
    begin_run(ParseNowRun(store_only=False, content_rev=REV))
    ctx = _make_ctx(_make_record(blocks=[], block_groups=[], record_id="rec-1"))

    await pipeline.apply(ctx)

    fields = doc_extraction.graph_provider.update_node.await_args.args[2]
    assert fields["indexingStatus"] == "EMPTY"
    assert fields["parseNowStatus"] == "EMPTY"
