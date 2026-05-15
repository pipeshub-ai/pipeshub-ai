"""Unit tests for app.modules.transformers.sink_orchestrator.SinkOrchestrator."""

import logging
from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest

from app.modules.transformers.sink_orchestrator import SinkOrchestrator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(record_id="rec-001"):
    """Build a mock TransformContext with a record carrying the given id."""
    record = MagicMock()
    record.id = record_id
    ctx = MagicMock()
    ctx.record = record
    ctx.settings = {}
    return ctx


def _make_orchestrator(
    graph_doc=None,
    vector_result=None,
):
    """Build a SinkOrchestrator with all sub-transformers mocked.

    Args:
        graph_doc: The document returned by graph_provider.get_document.
                   If None the record is treated as not found.
        vector_result: The return value of vector_store.apply.  Defaults to None
                       (truthy enough to continue).
    """
    graphdb = AsyncMock()
    blob_storage = AsyncMock()
    vector_store = AsyncMock()
    vector_store.apply = AsyncMock(return_value=vector_result)
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value=graph_doc)

    orch = SinkOrchestrator(
        graphdb=graphdb,
        blob_storage=blob_storage,
        vector_store=vector_store,
        graph_provider=graph_provider,
        logger=logging.getLogger("test-sink-orc")
    )
    # The Transformer base class does not set self.logger automatically in
    # all code paths.  Provide one so log calls don't blow up.
    orch.logger = MagicMock()

    return orch


# =========================================================================
# apply
# =========================================================================
class TestApply:
    """Tests for SinkOrchestrator.apply."""

    @pytest.mark.asyncio
    async def test_blob_storage_always_called_first(self):
        orch = _make_orchestrator(graph_doc={"indexingStatus": "COMPLETED"})
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.blob_storage.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_record_not_found_raises(self):
        orch = _make_orchestrator(graph_doc=None)
        ctx = _make_ctx("missing-id")

        with pytest.raises(Exception, match="not found"):
            await orch.apply(ctx)

    @pytest.mark.asyncio
    async def test_completed_skips_vector_and_graph(self):
        orch = _make_orchestrator(graph_doc={"indexingStatus": "COMPLETED"})
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_not_awaited()
        orch.graphdb.apply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_not_completed_runs_vector_and_graph(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "IN_PROGRESS"},
            vector_result=None,  # None is not False, so processing continues
        )
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once_with(ctx)
        orch.graphdb.apply.assert_awaited_once_with(ctx)

    @pytest.mark.asyncio
    async def test_missing_indexing_status_runs_vector_and_graph(self):
        """If the document has no indexingStatus field it is not 'COMPLETED'."""
        orch = _make_orchestrator(
            graph_doc={"someOtherField": "x"},
            vector_result=None,
        )
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once()
        orch.graphdb.apply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_vector_store_returns_false_skips_graph(self):
        """When vector_store.apply returns exactly False, graphdb must be skipped."""
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "QUEUED"},
            vector_result=False,
        )
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once()
        orch.graphdb.apply.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_vector_store_returns_true_continues_to_graph(self):
        orch = _make_orchestrator(
            graph_doc={"indexingStatus": "NOT_STARTED"},
            vector_result=True,
        )
        ctx = _make_ctx()

        await orch.apply(ctx)

        orch.vector_store.apply.assert_awaited_once()
        orch.graphdb.apply.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_graph_provider_called_with_correct_args(self):
        orch = _make_orchestrator(graph_doc={"indexingStatus": "COMPLETED"})
        ctx = _make_ctx("my-record-id")

        await orch.apply(ctx)

        orch.graph_provider.get_document.assert_awaited_once_with(
            "my-record-id", "records"
        )
