"""The runtime's adapters: the deferred publisher, classify's graph-backed IO, and assembly."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import SemanticMetadata
from app.modules.pipeline.leases import RecordLeases
from app.modules.pipeline.models import RETRYABLE_STAGES, Priority, StageJob
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.publisher import DeferredPublisher, PublisherNotBoundError
from app.modules.pipeline.runtime import PipelineRuntime, build_pipeline_runtime
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.classify_io import GraphClassifyIO
from app.services.resource_governor.models import ParseTier


def _job(**overrides: object) -> StageJob:
    fields: dict[str, object] = {
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1", "rec-2"), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    }
    fields.update(overrides)
    return StageJob.model_validate(fields)


class TestDeferredPublisher:
    @pytest.mark.asyncio
    async def test_refuses_to_publish_before_it_is_bound(self) -> None:
        with pytest.raises(PublisherNotBoundError):
            await DeferredPublisher().send_event("pipeline.classify", "stageJob", {})

    @pytest.mark.asyncio
    async def test_publishes_on_the_producers_loop(self) -> None:
        producer_loop = asyncio.new_event_loop()
        thread = threading.Thread(target=producer_loop.run_forever, daemon=True)
        thread.start()
        seen: list[asyncio.AbstractEventLoop] = []

        async def send_event(topic: str, event_type: str, payload: dict[str, object], key: str | None = None) -> bool:
            seen.append(asyncio.get_running_loop())
            return True

        producer = MagicMock()
        producer.send_event = send_event
        publisher = DeferredPublisher()
        publisher.bind(producer, producer_loop)
        try:
            assert await publisher.send_event("pipeline.classify", "stageJob", {"a": 1}, key="c") is True
            assert seen == [producer_loop]
        finally:
            producer_loop.call_soon_threadsafe(producer_loop.stop)
            thread.join(timeout=5)
            producer_loop.close()


def _io(**services: object) -> GraphClassifyIO:
    graph = services.get("graph") or AsyncMock()
    return GraphClassifyIO(
        _job(),
        Deadline(60),
        policy=DEFAULT_POLICY,
        graph=graph,  # type: ignore[arg-type]
        blob_storage=services.get("blob") or AsyncMock(),  # type: ignore[arg-type]
        vector_store=services.get("vectors") or AsyncMock(),  # type: ignore[arg-type]
        taxonomy=services.get("taxonomy") or AsyncMock(),  # type: ignore[arg-type]
        classifier=services.get("classifier") or AsyncMock(),  # type: ignore[arg-type]
        config_service=AsyncMock(),
        record_from_document=lambda doc: MagicMock(id=doc["_key"]),
        record_leases=RecordLeases(),
    )


class TestGraphClassifyIO:
    @pytest.mark.asyncio
    async def test_only_records_still_on_the_revision_are_current(self) -> None:
        graph = AsyncMock()
        docs = {"rec-1": {"_key": "rec-1", "contentRev": "rev-a"}, "rec-2": {"_key": "rec-2", "contentRev": "rev-b"}}
        graph.get_document = AsyncMock(side_effect=lambda rid, _c: docs.get(rid))
        assert await _io(graph=graph).current_record_ids(_job()) == ["rec-1"]

    @pytest.mark.asyncio
    async def test_departments_are_read_once_per_job(self) -> None:
        graph = AsyncMock()
        graph.get_departments = AsyncMock(return_value=["Engineering"])
        io = _io(graph=graph)
        assert await io.departments("org-1") == ["Engineering"]
        assert await io.departments("org-1") == ["Engineering"]
        graph.get_departments.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_blocks_come_from_the_stored_record(self) -> None:
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value={"block_containers": {"blocks": [], "block_groups": []}})
        blocks = await _io(blob=blob).load_blocks(_job())
        assert blocks is not None and blocks.blocks == []
        blob.get_record_from_storage = AsyncMock(return_value=None)
        assert await _io(blob=blob).load_blocks(_job()) is None

    @pytest.mark.asyncio
    async def test_save_writes_content_once_and_edges_per_record(self) -> None:
        graph, blob, vectors, taxonomy = AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock()
        graph.get_document = AsyncMock(return_value={"_key": "rec-1", "contentRev": "rev-a"})
        metadata = SemanticMetadata(summary="A VPN plan.", categories=["Security"])
        await _io(graph=graph, blob=blob, vectors=vectors, taxonomy=taxonomy).save(_job(), ["rec-1", "rec-2"], metadata)
        blob.save_semantic_metadata.assert_awaited_once()
        assert blob.save_semantic_metadata.await_args.args[:3] == ("org-1", "rec-1", "vr-1")
        vectors.index_record_summary.assert_awaited_once()
        assert [call.args[0] for call in taxonomy.write_taxonomy.await_args_list] == ["rec-1", "rec-2"]

    @pytest.mark.asyncio
    async def test_an_empty_summary_writes_no_summary_vector(self) -> None:
        vectors = AsyncMock()
        graph = AsyncMock()
        graph.get_document = AsyncMock(return_value={"_key": "rec-1", "contentRev": "rev-a"})
        await _io(graph=graph, vectors=vectors).save(_job(), ["rec-1"], SemanticMetadata(summary="  ", categories=[]))
        vectors.index_record_summary.assert_not_awaited()


def _runtime() -> PipelineRuntime:
    return build_pipeline_runtime(
        logger=MagicMock(),
        config_service=AsyncMock(),
        graph=AsyncMock(),
        blob_storage=AsyncMock(),  # type: ignore[arg-type]
        vector_store=AsyncMock(),  # type: ignore[arg-type]
        taxonomy=AsyncMock(),  # type: ignore[arg-type]
        classifier=AsyncMock(),
        record_from_document=MagicMock(),
    )


def test_the_runtime_registers_classification_after_the_indexer() -> None:
    runtime = _runtime()
    assert runtime.registry.names() == ("classify",)
    assert runtime.registry.is_external("embed")
    assert runtime.registry.topic_for("classify") == "pipeline.classify"
    assert runtime.stage_limits["classify"] >= 1
    assert not runtime.publisher.bound


def test_health_reports_each_stage_with_zeroed_outcomes() -> None:
    stats = _runtime().stats()
    assert stats["publisher_bound"] is False
    classify = stats["stages"]["classify"]
    assert classify["topic"] == "pipeline.classify"
    assert classify["limit"] >= 1
    assert set(classify["outcomes"]) >= {"completed", "unchanged", "retry", "paused", "failed", "abandoned"}
    assert not any(classify["outcomes"].values())


def test_every_retryable_stage_is_registered_with_the_status_field_the_api_selects_on() -> None:
    registry = _runtime().registry
    for name, field in RETRYABLE_STAGES.items():
        assert registry.get(name).headline is field
