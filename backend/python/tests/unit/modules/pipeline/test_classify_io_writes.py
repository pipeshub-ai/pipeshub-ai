"""GraphClassifyIO writes the stored record only while the record is on the job's revision."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.blocks import SemanticMetadata
from app.modules.pipeline.leases import RecordLeases, RevisionSuperseded
from app.modules.pipeline.models import Priority, StageJob
from app.modules.pipeline.policy import DEFAULT_POLICY
from app.modules.pipeline.stage import Deadline
from app.modules.pipeline.stages.classify_io import GraphClassifyIO
from app.services.resource_governor.models import ParseTier


def _job() -> StageJob:
    return StageJob.model_validate({
        "stage": "classify", "stage_version": 1, "org_id": "org-1", "virtual_record_id": "vr-1",
        "rev": "rev-a", "record_ids": ("rec-1",), "connector_id": "conn-1", "tier": ParseTier.LIGHT,
        "priority": Priority.BULK, "trigger": "embed", "text_digest": "t", "blocks_digest": "b",
        "text_chars": 10, "has_tables": False, "has_images": False,
    })


def _io(graph_rev: str, stored: dict[str, object] | None = None) -> tuple[GraphClassifyIO, AsyncMock]:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"contentRev": graph_rev})
    blob = MagicMock()
    blob.save_semantic_metadata = AsyncMock()
    blob.get_record_from_storage = AsyncMock(return_value=stored)
    io = GraphClassifyIO(
        _job(), Deadline(30), policy=DEFAULT_POLICY, graph=graph, blob_storage=blob,
        vector_store=MagicMock(), taxonomy=MagicMock(), classifier=AsyncMock(),
        config_service=MagicMock(), record_from_document=MagicMock(), record_leases=RecordLeases(),
    )
    return io, blob.save_semantic_metadata


@pytest.mark.asyncio
async def test_a_record_that_moved_to_a_newer_revision_is_not_written() -> None:
    io, save = _io(graph_rev="rev-b")
    with pytest.raises(RevisionSuperseded):
        await io.save(_job(), ["rec-1"], SemanticMetadata(summary="s"))
    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="s"))
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_kept_classification_is_restored_only_while_its_state_is_unchanged() -> None:
    io, save = _io(graph_rev="rev-a")

    async def superseded() -> bool:
        return False

    async def unchanged() -> bool:
        return True

    with pytest.raises(RevisionSuperseded):
        await io.restore_stored_metadata(_job(), SemanticMetadata(summary="old"), still_current=superseded)
    save.assert_not_awaited()
    await io.restore_stored_metadata(_job(), SemanticMetadata(summary="old"), still_current=unchanged)
    save.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_stored_record_is_read_once_per_job() -> None:
    io, _save = _io(graph_rev="rev-a", stored={"block_containers": {"blocks": [], "block_groups": []}})
    assert not await io.stored_metadata_present(_job())
    assert await io.load_blocks(_job()) is not None
    io._blob_storage.get_record_from_storage.assert_awaited_once()  # type: ignore[attr-defined]
