"""``index()`` projects a record's properties at the end of every path that completes, and never because
of -- or at the cost of -- the indexing itself."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.exceptions.indexing_exceptions import RecordDeletedError
from app.models.entities import Record, RecordType
from app.modules.record_metadata import projection as projection_module
from app.modules.record_metadata.projection import PROJECTION_FLAG, Projection
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.stored_write import COW
from app.modules.transformers.transformer import TransformContext
from app.services.featureflag.platform_settings import ArtifactSettings
from tests.unit.modules.record_metadata.projection_world import (
    FakeGraph,
    add_ticket,
    ticket,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.unit


def _record() -> Record:
    return Record(
        id="rec-1", record_name="r", record_type=RecordType.FILE, external_record_id="e", version=1, origin="UPLOAD",
        connector_name=Connectors.KNOWLEDGE_BASE, connector_id="kb-1", org_id="org-1", virtual_record_id="vr-1",
        mime_type="text/plain",
    )


def _sink(status: str = "NOT_STARTED", graph: Any = None) -> Any:  # noqa: ANN401
    graph = graph or AsyncMock()
    if isinstance(graph, AsyncMock):
        graph.get_document = AsyncMock(return_value={"id": "rec-1", "indexingStatus": status, "contentRev": "0123456789abcdef"})
    graph.batch_update_nodes = AsyncMock(return_value=True)
    vectors = AsyncMock()
    vectors.apply = AsyncMock(return_value=True)
    orch = sink_module.SinkOrchestrator(
        graphdb=AsyncMock(), blob_storage=AsyncMock(), vector_store=vectors, graph_provider=graph,
        logger=logging.getLogger("t"), config_service=MagicMock(),
    )
    orch._save_reconciliation_metadata = AsyncMock()
    orch._sync_record_identity_entities = AsyncMock()
    orch._update_indexing_status = AsyncMock()
    orch.image_describer.annotate = AsyncMock(return_value=0)
    orch.artifact_writer = MagicMock()
    orch.artifact_writer.write_revision = AsyncMock(return_value=None)
    orch.write_blob = AsyncMock()
    return orch


@pytest.fixture(autouse=True)
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(PROJECTION_FLAG, "true")


@pytest.fixture
def projected() -> Iterator[AsyncMock]:
    with patch.object(sink_module, "project_properties", AsyncMock(return_value=Projection("written", 1))) as spy, \
            patch.object(sink_module, "artifact_settings", AsyncMock(return_value=ArtifactSettings(COW, "g"))), \
            patch.object(sink_module, "is_artifact_write_enabled", AsyncMock(return_value=True)):
        yield spy


async def test_runs_on_the_normal_path_after_completed_is_written(projected: AsyncMock) -> None:
    orch = _sink("NOT_STARTED")
    order: list[str] = []
    orch._update_indexing_status = AsyncMock(side_effect=lambda ctx: order.append("status"))
    projected.side_effect = lambda **kw: order.append("project") or Projection("written", 1)

    await orch.index(TransformContext(record=_record()))

    assert order == ["status", "project"]


async def test_runs_when_the_record_was_already_completed(projected: AsyncMock) -> None:
    orch = _sink("COMPLETED")
    await orch.index(TransformContext(record=_record()))
    orch.vector_store.apply.assert_not_awaited()
    projected.assert_awaited_once()


async def test_runs_with_skip_blob(projected: AsyncMock) -> None:
    orch = _sink("COMPLETED")
    await orch.index(TransformContext(record=_record(), settings={"skip_blob": True}))
    orch.vector_store.apply.assert_awaited_once()
    projected.assert_awaited_once()


@pytest.mark.parametrize("setting", ["sink_only", "skip_vector_store"])
async def test_runs_in_sink_only_mode(projected: AsyncMock, setting: str) -> None:
    orch = _sink("NOT_STARTED")
    await orch.index(TransformContext(record=_record(), settings={setting: True}))
    orch.vector_store.apply.assert_not_awaited()
    projected.assert_awaited_once()


async def test_runs_with_artifact_writing_off(projected: AsyncMock) -> None:
    orch = _sink("NOT_STARTED")
    with patch.object(sink_module, "is_artifact_write_enabled", AsyncMock(return_value=False)):
        await orch.index(TransformContext(record=_record()))
    orch.artifact_writer.write_revision.assert_not_awaited()
    projected.assert_awaited_once()


async def test_runs_exactly_once_per_index_call(projected: AsyncMock) -> None:
    for status, settings in (("NOT_STARTED", {}), ("COMPLETED", {}), ("COMPLETED", {"skip_blob": True}),
                             ("NOT_STARTED", {"sink_only": True})):
        projected.reset_mock()
        await _sink(status).index(TransformContext(record=_record(), settings=dict(settings)))
        assert projected.await_count == 1, (status, settings)


async def test_a_record_deleted_while_indexing_is_not_projected(projected: AsyncMock) -> None:
    orch = _sink()
    orch.graph_provider.get_document = AsyncMock(return_value=None)
    with pytest.raises(RecordDeletedError):
        await orch.index(TransformContext(record=_record()))
    projected.assert_not_awaited()


async def test_flag_off_does_nothing(projected: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(PROJECTION_FLAG)
    await _sink().index(TransformContext(record=_record()))
    projected.assert_not_awaited()


async def test_projection_failure_never_fails_indexing(projected: AsyncMock) -> None:
    projected.side_effect = RuntimeError("graph down")
    orch = _sink()
    with patch.object(sink_module, "record_property_projection") as counted:
        await orch.index(TransformContext(record=_record()))
    orch._update_indexing_status.assert_awaited_once()
    counted.assert_called_once_with("error")


async def test_a_projection_that_hangs_is_cut_off(projected: AsyncMock) -> None:
    async def hang(**_: Any) -> Projection:  # noqa: ANN401
        await asyncio.sleep(60)
        return Projection("written")

    projected.side_effect = hang
    with patch.object(sink_module, "PROJECTION_TIMEOUT_SECONDS", 0.05):
        await asyncio.wait_for(_sink().index(TransformContext(record=_record())), timeout=5)


async def test_the_caller_values_reach_the_projection(projected: AsyncMock) -> None:
    sentinel = object()
    await _sink().index(TransformContext(record=_record(), settings={projection_module.PROVIDED_PROPERTIES: sentinel}))
    assert projected.await_args.kwargs["provided"] is sentinel
    projected.reset_mock()
    await _sink().index(TransformContext(record=_record()))
    assert projected.await_args.kwargs["provided"] is projection_module.NOT_PROVIDED


async def test_end_to_end_a_ticket_is_projected_by_index_and_a_second_index_writes_nothing() -> None:
    """Through the real projection, on the real sink, against the in-memory graph."""
    graph = FakeGraph()
    t = ticket()
    add_ticket(graph, t, indexingStatus="COMPLETED", contentRev="0123456789abcdef")
    orch = _sink("COMPLETED", graph)
    with patch.object(sink_module, "artifact_settings", AsyncMock(return_value=ArtifactSettings(COW, "g"))), \
            patch.object(sink_module, "is_artifact_write_enabled", AsyncMock(return_value=False)):
        from app.events.processor import convert_record_dict_to_record

        def ctx() -> TransformContext:
            return TransformContext(record=convert_record_dict_to_record(graph.records[t.id]))

        await orch.index(ctx())
        assert graph.props(t.id)["status"] == "OPEN" and len(graph.writes) == 1
        graph.reads.clear()
        await orch.index(ctx())
        assert len(graph.writes) == 1 and graph.write_calls == 1
