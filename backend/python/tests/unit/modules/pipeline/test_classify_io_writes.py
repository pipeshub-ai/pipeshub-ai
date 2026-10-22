"""The record stages write the stored record only while the record is on the job's revision,
and each writes only its own section of it, whichever finishes first."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.events.processor import convert_record_dict_to_record
from app.models.blocks import SemanticMetadata
from app.models.entities import Record
from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.pipeline.leases import RecordLeases, RevisionSuperseded
from app.modules.pipeline.models import Priority, StageJob
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.classify_io import GraphClassifyIO
from app.modules.pipeline.stages.entities_io import GraphEntitiesIO
from app.modules.pipeline.stages.record_io import StoredRecordMissing
from app.modules.transformers.transformer import TransformContext
from app.services.resource_governor.models import ParseTier


def _job(stage: str = "classify") -> StageJob:
    return StageJob.model_validate({
        "stage": stage, "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    })


class _Blob:
    """An in-memory stored record: ``save_semantic_metadata`` replaces its metadata, as the real one does."""

    def __init__(self, stored: dict[str, Any] | None) -> None:
        self.stored = stored
        self.saves = 0
        self.get_record_from_storage = AsyncMock(side_effect=self._get)
        self.save_semantic_metadata = AsyncMock(side_effect=self._save)

    async def _get(self, virtual_record_id: str, org_id: str) -> dict[str, Any] | None:
        return None if self.stored is None else {**self.stored}

    async def _save(self, org_id: str, record_id: str, virtual_record_id: str, metadata: dict[str, Any]) -> None:
        assert self.stored is not None
        self.stored = {**self.stored, "semantic_metadata": metadata}
        self.saves += 1


def _graph(rev: str) -> MagicMock:
    graph = MagicMock()
    graph.get_document = AsyncMock(
        return_value={
            "_key": "rec-1", "orgId": "org-1", "contentRev": rev, "recordName": "a.txt", "recordType": "FILE",
            "externalRecordId": "ext-1", "connectorId": "conn-1", "mimeType": "text/plain",
            "connectorName": "KB", "origin": "UPLOAD",
        }
    )
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    return graph


def _sink() -> MagicMock:
    sink = MagicMock()
    sink.prepare_enrichment = AsyncMock()
    sink.write_enrichment = AsyncMock()
    sink.persist_named_entities = AsyncMock(return_value="written")
    sink.entity_resolution_mode = MagicMock(return_value="apply")
    return sink


def _record(document: dict[str, Any]) -> Record:
    return convert_record_dict_to_record(document)


def _classify_io(blob: _Blob, rev: str = "rev-a", sink: MagicMock | None = None) -> GraphClassifyIO:
    return GraphClassifyIO(
        _job(), Deadline(30), policy=DEFAULT_POLICY, graph=_graph(rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=sink or _sink(), classifier=AsyncMock(), config_service=MagicMock(),
        record_from_document=_record, record_leases=RecordLeases(),
    )


def _entities_io(blob: _Blob, rev: str = "rev-a") -> GraphEntitiesIO:
    return GraphEntitiesIO(
        _job("entities"), Deadline(30), policy=DEFAULT_POLICY, graph=_graph(rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=_sink(), config_service=MagicMock(), record_from_document=_record, record_leases=RecordLeases(),
    )


def _extraction() -> NamedEntityExtraction:
    return NamedEntityExtraction(status="COMPLETED", strategy="deterministic")


@pytest.mark.asyncio
async def test_a_record_that_moved_to_a_newer_revision_is_not_written() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = _classify_io(blob, rev="rev-b")
    with pytest.raises(RevisionSuperseded):
        await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))
    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="s"))
    with pytest.raises(RevisionSuperseded):
        await _entities_io(blob, rev="rev-b").save(_job("entities"), ["rec-1"], _extraction())
    assert blob.saves == 0


@pytest.mark.asyncio
async def test_a_kept_classification_is_restored_only_while_its_state_is_unchanged() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = _classify_io(blob)

    async def superseded() -> bool:
        return False

    async def unchanged() -> bool:
        return True

    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="old"), still_current=superseded)
    assert blob.saves == 0
    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="old"), still_current=unchanged)
    assert blob.saves == 1


@pytest.mark.asyncio
async def test_no_stored_record_is_reported_as_missing() -> None:
    with pytest.raises(StoredRecordMissing):
        await _classify_io(_Blob(None)).restore_stored_metadata(_job(), SemanticMetadata(summary="s"))


@pytest.mark.asyncio
async def test_the_stored_record_is_read_once_per_job_before_writing() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = _classify_io(blob)
    assert not await io.stored_metadata_present(_job())
    assert await io.load_blocks(_job()) is not None
    blob.get_record_from_storage.assert_awaited_once()


@pytest.mark.parametrize("classify_first", [True, False])
@pytest.mark.asyncio
async def test_classify_and_entities_keep_each_others_section(classify_first: bool) -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}, "semantic_metadata": None})
    classify, entities = _classify_io(blob), _entities_io(blob)

    async def run_classify() -> None:
        assert await classify.current_record_ids(_job()) == ["rec-1"]
        await classify.save(_job(), ["rec-1"], SemanticMetadata(summary="s", topics=["VPN"], categories=["Security"]))

    async def run_entities() -> None:
        assert await entities.current_record_ids(_job("entities")) == ["rec-1"]
        assert await entities.save(_job("entities"), ["rec-1"], _extraction()) == "written"

    for step in (run_classify, run_entities) if classify_first else (run_entities, run_classify):
        await step()

    stored = blob.stored["semantic_metadata"]  # type: ignore[index]
    assert stored["summary"] == "s" and stored["topics"] == ["VPN"]
    assert stored["named_entities"]["status"] == "COMPLETED"
    assert await _classify_io(blob).stored_metadata_present(_job())
    assert await _entities_io(blob).stored_entities_present(_job("entities"))


@pytest.mark.asyncio
async def test_classify_writes_the_enrichment_after_the_stored_record() -> None:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    sink = _sink()
    io = _classify_io(blob, sink=sink)
    await io.current_record_ids(_job())
    await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))
    sink.prepare_enrichment.assert_awaited_once()
    sink.write_enrichment.assert_awaited_once()
    assert blob.saves == 1


class _Leases:
    """A lease manager that records who holds what."""

    def __init__(self) -> None:
        self.held: set[str] = set()

    async def try_acquire(self, pool: str, holder: str, limit: int, lease_seconds: float) -> bool:
        self.held.add(pool)
        return True

    async def renew(self, pool: str, holder: str, lease_seconds: float) -> bool:
        return True

    async def release(self, pool: str, holder: str) -> None:
        self.held.discard(pool)


def _two_records(second_rev: str) -> MagicMock:
    """rec-1 on the job's revision; rec-2 (a bound copy) on ``second_rev``."""
    graph = _graph("rev-a")
    base = graph.get_document.return_value

    async def document(record_id: str, collection: str, **_: object) -> dict[str, Any]:
        rev = "rev-a" if record_id == "rec-1" else second_rev
        return {**base, "_key": record_id, "contentRev": rev}

    graph.get_document = AsyncMock(side_effect=document)
    return graph


def _bound_leases() -> tuple[RecordLeases, _Leases]:
    leases, manager = RecordLeases(), _Leases()
    leases.bind(manager)  # type: ignore[arg-type]
    return leases, manager


@pytest.mark.asyncio
@pytest.mark.parametrize("second_rev", ["rev-a", "rev-b"])
async def test_classification_outputs_are_written_under_each_records_lease(second_rev: str) -> None:  # review: revision fence
    leases, manager = _bound_leases()
    seen: list[tuple[str, bool]] = []
    sink = _sink()

    async def write_enrichment(ctx: TransformContext, index_summary: bool = True) -> None:
        seen.append((ctx.record.id, f"record:{ctx.record.id}" in manager.held))

    sink.write_enrichment = AsyncMock(side_effect=write_enrichment)
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = GraphClassifyIO(
        _job(), Deadline(30), policy=DEFAULT_POLICY, graph=_two_records(second_rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=sink, classifier=AsyncMock(), config_service=MagicMock(),
        record_from_document=_record, record_leases=leases,
    )
    await io.save(_job(), ["rec-1", "rec-2"], SemanticMetadata(summary="s"))
    # Each write ran holding that record's lease; a copy on a newer revision got nothing.
    expected = [("rec-1", True), ("rec-2", True)] if second_rev == "rev-a" else [("rec-1", True)]
    assert seen == expected
    assert manager.held == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("second_rev", ["rev-a", "rev-b"])
async def test_named_entities_are_persisted_under_each_records_lease(second_rev: str) -> None:
    leases, manager = _bound_leases()
    seen: list[tuple[str, bool]] = []
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    io = GraphEntitiesIO(
        _job("entities"), Deadline(30), policy=DEFAULT_POLICY, graph=_two_records(second_rev), blob_storage=blob,  # type: ignore[arg-type]
        sink=_sink(), config_service=MagicMock(), record_from_document=_record, record_leases=leases,
    )

    async def persist(ctx: TransformContext, extraction: NamedEntityExtraction) -> str:
        seen.append((ctx.record.id, f"record:{ctx.record.id}" in manager.held))
        return "kept"

    io._sink.persist_named_entities = AsyncMock(side_effect=persist)  # type: ignore[attr-defined]
    assert await io.save(_job("entities"), ["rec-1", "rec-2"], _extraction()) == "kept"
    expected = [("rec-1", True), ("rec-2", True)] if second_rev == "rev-a" else [("rec-1", True)]
    assert seen == expected


@pytest.mark.asyncio
async def test_classification_and_entities_are_written_under_the_jobs_virtual_record() -> None:
    # The summary point is keyed by the record's virtual record id; written without one it
    # lands under "None", outside the record, where search filters and deletes never reach it.
    seen: list[str | None] = []
    sink = _sink()

    async def write_enrichment(ctx: TransformContext, index_summary: bool = True) -> None:
        seen.append(ctx.record.virtual_record_id)

    async def persist(ctx: TransformContext, extraction: NamedEntityExtraction) -> str:
        seen.append(ctx.record.virtual_record_id)
        return "kept"

    sink.write_enrichment = AsyncMock(side_effect=write_enrichment)
    sink.persist_named_entities = AsyncMock(side_effect=persist)
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    await _classify_io(blob, sink=sink).save(_job(), ["rec-1"], SemanticMetadata(summary="s"))
    entities = GraphEntitiesIO(
        _job("entities"), Deadline(30), policy=DEFAULT_POLICY, graph=_graph("rev-a"), blob_storage=blob,  # type: ignore[arg-type]
        sink=sink, config_service=MagicMock(), record_from_document=_record, record_leases=RecordLeases(),
    )
    await entities.save(_job("entities"), ["rec-1"], _extraction())
    assert seen == ["vr-1", "vr-1"]
