"""Entity resolution runs in the classify stage, right after classification and before the
stored record, summary and graph writes all consume the names (it used to run inline on
the service path, which now only indexes and hands the record off)."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.processor import convert_record_dict_to_record
from app.exceptions.indexing_exceptions import EntityLookupUnavailableError
from app.models.blocks import SemanticMetadata
from app.modules.pipeline.leases import RecordLeases
from app.modules.pipeline.models import Priority, StageJob, StageOutcome
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.classify import ClassifyStage
from app.modules.pipeline.stages.classify_io import GraphClassifyIO
from app.services.resource_governor.models import ParseTier
from unit.events.test_orchestrator_flow import (
    _make_event_data,
    _make_event_processor,
    _make_parse_result,
)


def _metadata() -> SemanticMetadata:
    return SemanticMetadata(
        summary="A summary", categories=["QA"], topics=["Bug bash"], languages=["en"], departments=[],
    )


def _job() -> StageJob:
    return StageJob.model_validate({
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    })


def _io(sink: MagicMock, order: list[str], classification: SemanticMetadata | None) -> GraphClassifyIO:
    graph = AsyncMock()
    graph.get_document = AsyncMock(return_value={
        "_key": "rec-1", "orgId": "org-1", "contentRev": "rev-a", "recordName": "a.txt",
        "externalRecordId": "x", "connectorId": "conn-1", "mimeType": "text/plain",
    })
    graph.get_departments = AsyncMock(return_value=[])
    blob = AsyncMock()
    blob.get_record_from_storage = AsyncMock(return_value={"block_containers": {"blocks": [], "block_groups": []}})
    blob.config_service.get_config = AsyncMock(return_value={})

    async def rewrite(org_id, record_id, vrid, transform, **_):  # noqa: ANN202
        new = await transform({"semantic_metadata": None})
        order.append("blob")
        return {"semantic_metadata": new}

    blob.rewrite_semantic_metadata = AsyncMock(side_effect=rewrite)
    return GraphClassifyIO(
        _job(), Deadline(30), policy=DEFAULT_POLICY, graph=graph, blob_storage=blob, sink=sink,
        classifier=AsyncMock(return_value=classification), config_service=MagicMock(),
        record_from_document=convert_record_dict_to_record, record_leases=RecordLeases(),
    )


@pytest.mark.asyncio
async def test_resolution_runs_before_the_stored_record_summary_and_graph() -> None:
    order: list[str] = []
    sink = MagicMock()
    sink.prepare_enrichment = AsyncMock(side_effect=lambda ctx: order.append("resolve"))
    sink.write_enrichment = AsyncMock(side_effect=lambda ctx, **_: order.append("summary+graph"))
    sink.entity_resolution_mode = MagicMock(return_value="apply")
    classification = _metadata()

    with patch("app.modules.pipeline.stages.classify_io.load_ai_models", AsyncMock(return_value={})):
        result = await ClassifyStage().run(_job(), _io(sink, order, classification))

    assert result.outcome is StageOutcome.COMPLETED
    assert order == ["resolve", "blob", "summary+graph"]
    assert sink.prepare_enrichment.await_args.args[0].record.semantic_metadata is classification


@pytest.mark.asyncio
async def test_no_metadata_means_no_resolution_call() -> None:
    sink = MagicMock()
    sink.prepare_enrichment = AsyncMock()
    result = await ClassifyStage().run(_job(), _io(sink, [], None))
    assert result.outcome is StageOutcome.SKIPPED
    sink.prepare_enrichment.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_deferred_resolution_is_retried_not_failed() -> None:
    order: list[str] = []
    sink = MagicMock()
    sink.prepare_enrichment = AsyncMock(side_effect=EntityLookupUnavailableError("entity index unreachable"))
    sink.write_enrichment = AsyncMock()
    result = await ClassifyStage().run(_job(), _io(sink, order, _metadata()))
    assert result.outcome is StageOutcome.RETRY
    # Nothing of the enrichment is written before resolution succeeds.
    assert order == []
    sink.write_enrichment.assert_not_awaited()


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_the_service_path_resolves_nothing_inline() -> None:
    parsing_client = MagicMock()
    parsing_client.circuit_open = False
    parsing_client.parse = AsyncMock(return_value=_make_parse_result())
    sink = MagicMock()
    sink.index = AsyncMock()
    sink.resolve_entities = AsyncMock()
    sink.prepare_enrichment = AsyncMock()
    ep = _make_event_processor(parsing_client=parsing_client, extraction_client=MagicMock(), sink_orchestrator=sink)

    async for _event in ep.on_event(_make_event_data()):
        pass

    sink.resolve_entities.assert_not_awaited()
    sink.prepare_enrichment.assert_not_awaited()
    ep.stage_ingress.on_indexed.assert_awaited_once()
