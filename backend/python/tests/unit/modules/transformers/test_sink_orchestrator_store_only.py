"""``SinkOrchestrator.index`` in a parse-now run: the parse-now marker, and the store-only mode that
stores a record's content and nothing else (automatic indexing off)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import ANY, AsyncMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.exceptions.indexing_exceptions import IndexingError
from app.modules.artifacts import store as store_module
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.stored_write import RUN_SUPERSEDED
from app.modules.transformers.transformer import (
    PARSE_NOW,
    STORE_ONLY,
    ReconciliationContext,
    TransformContext,
)
from tests.unit.modules.transformers.test_sink_orchestrator_artifacts import (
    REV,
    Graph,
    _graph,
    _index,
    _orchestrator,
    _record,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

RECORDS = CollectionNames.RECORDS.value
STORED_MARKER = {"parseNowStatus": "STORED", "parseNowRev": REV, "parseNowReason": None, "parseNowAt": ANY}


@pytest.fixture(autouse=True)
def _quiet_side_effects() -> Iterator[None]:
    store_module.ARTIFACT_ROUTES.reset()
    with patch.object(sink_module, "notify_record_indexed", AsyncMock()):
        yield


def _ctx(*settings: str, **extra: Any) -> TransformContext:  # noqa: ANN401
    return TransformContext(record=_record(), settings=dict.fromkeys(settings, True), **extra)


def _with_marker_writes(graph: Graph, order: list[str] | None = None) -> AsyncMock:
    async def update_node(key: str, collection: str, fields: dict[str, Any]) -> bool:
        if order is not None:
            order.append("marker")
        return True

    graph.provider.update_node = AsyncMock(side_effect=update_node)
    return graph.provider.update_node


class TestStoreOnly:
    async def test_stores_the_content_and_embeds_nothing(self) -> None:
        # B1
        graph = _graph(indexingStatus="AUTO_INDEX_OFF")
        orch = _orchestrator(graph)
        marker = _with_marker_writes(graph)

        await _index(orch, _ctx(PARSE_NOW, STORE_ONLY))

        orch.blob_storage.apply.assert_awaited_once()
        orch.artifact_writer.write_revision.assert_awaited_once()
        assert graph.mapping["currentRev"] == REV  # the revision pointer moved
        orch.vector_store.apply.assert_not_awaited()
        marker.assert_awaited_once_with("rec-1", RECORDS, STORED_MARKER)

    async def test_the_marker_follows_the_blob_and_the_pointer(self) -> None:
        # B4
        order: list[str] = []
        graph = _graph(indexingStatus="AUTO_INDEX_OFF")
        orch = _orchestrator(graph)
        _with_marker_writes(graph, order)
        orch.blob_storage.apply = AsyncMock(side_effect=lambda ctx: order.append("blob"))
        orch.artifact_writer.write_revision = AsyncMock(side_effect=lambda *a, **kw: order.append("artifacts") or _written())
        graph.provider.update_node_fields_if_match = AsyncMock(side_effect=lambda *a, **kw: order.append("flip") or True)

        await _index(orch, _ctx(PARSE_NOW, STORE_ONLY))

        assert order == ["blob", "artifacts", "flip", "marker"]

    async def test_the_record_keeps_its_status_and_no_status_is_written(self) -> None:
        # B3, B7, B8
        graph = _graph(indexingStatus="AUTO_INDEX_OFF", reason="Automatic indexing is off")
        orch = _orchestrator(graph)
        _with_marker_writes(graph)

        await _index(orch, _ctx(PARSE_NOW, STORE_ONLY))

        graph.provider.batch_update_nodes.assert_not_awaited()  # neither COMPLETED nor NOT_STARTED
        written = [call.args[2] for call in graph.provider.update_node.await_args_list]
        assert all(not {"indexingStatus", "reason", "hasIndexedCopy", "lastIndexTimestamp"} & set(w) for w in written)

    async def test_no_reconciliation_metadata_no_entity_points(self) -> None:
        # B2: a later index must embed every block, and a store-only run has no title or group point to write.
        graph = _graph(indexingStatus="AUTO_INDEX_OFF")
        orch = _orchestrator(graph)
        _with_marker_writes(graph)
        ctx = _ctx(PARSE_NOW, STORE_ONLY, reconciliation_context=ReconciliationContext(new_metadata={"h": {}}))

        await _index(orch, ctx)

        orch._save_reconciliation_metadata.assert_not_awaited()
        orch._sync_record_identity_entities.assert_not_awaited()

    async def test_a_marker_that_does_not_take_fails_the_attempt(self) -> None:
        graph = _graph(indexingStatus="AUTO_INDEX_OFF")
        orch = _orchestrator(graph)
        graph.provider.update_node = AsyncMock(return_value=False)

        with pytest.raises(IndexingError):
            await _index(orch, _ctx(PARSE_NOW, STORE_ONLY))

    async def test_a_record_without_a_revision_cannot_be_marked(self) -> None:
        graph = _graph(indexingStatus="AUTO_INDEX_OFF", contentRev=None)
        orch = _orchestrator(graph)
        marker = _with_marker_writes(graph)

        await _index(orch, _ctx(PARSE_NOW, STORE_ONLY))

        marker.assert_not_awaited()

    async def test_a_write_that_lost_to_a_newer_one_is_not_marked(self) -> None:
        graph = _graph(indexingStatus="AUTO_INDEX_OFF")
        orch = _orchestrator(graph)
        marker = _with_marker_writes(graph)

        async def apply(ctx: TransformContext) -> None:
            ctx.settings[RUN_SUPERSEDED] = True

        orch.blob_storage.apply = AsyncMock(side_effect=apply)

        await _index(orch, _ctx(PARSE_NOW, STORE_ONLY))

        marker.assert_not_awaited()


class TestParseNowOfAnIndexedRecord:
    async def test_marks_when_stored_then_indexes_as_usual(self) -> None:
        # B4, B11: readable before the embedding completes, then one normal indexing run.
        order: list[str] = []
        graph = _graph(indexingStatus="IN_PROGRESS")
        orch = _orchestrator(graph)
        _with_marker_writes(graph, order)
        orch.blob_storage.apply = AsyncMock(side_effect=lambda ctx: order.append("blob"))
        orch.vector_store.apply = AsyncMock(side_effect=lambda ctx: order.append("vectors") or True)
        graph.provider.batch_update_nodes = AsyncMock(side_effect=lambda *a, **kw: order.append("status") or True)

        await _index(orch, _ctx(PARSE_NOW))

        assert order == ["blob", "marker", "vectors", "status"]
        orch.vector_store.apply.assert_awaited_once()
        orch._save_reconciliation_metadata.assert_awaited_once()

    async def test_a_marker_that_does_not_take_does_not_fail_indexing(self) -> None:
        graph = _graph(indexingStatus="IN_PROGRESS")
        orch = _orchestrator(graph)
        graph.provider.update_node = AsyncMock(side_effect=RuntimeError("graph blip"))

        await _index(orch, _ctx(PARSE_NOW))

        orch.vector_store.apply.assert_awaited_once()

    async def test_an_ordinary_run_writes_no_marker(self) -> None:
        graph = _graph(indexingStatus="IN_PROGRESS")
        orch = _orchestrator(graph)
        marker = _with_marker_writes(graph)

        await _index(orch, _ctx())

        marker.assert_not_awaited()


def _written() -> Any:  # noqa: ANN401
    from tests.unit.modules.transformers.test_sink_orchestrator_artifacts import (
        _written as written,
    )

    return written()
