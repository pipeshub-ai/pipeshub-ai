"""StageIngress: the indexer's completion becomes the embed prerequisite for the record's revision."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.pipeline.fingerprint import content_facts
from app.modules.pipeline.ingress import StageIngress
from app.modules.pipeline.models import Priority, RecordView
from app.services.resource_governor.models import ParseTier


def _record(connector: Connectors = Connectors.KNOWLEDGE_BASE) -> MagicMock:
    record = MagicMock()
    record.id, record.org_id, record.virtual_record_id = "rec-1", "org-1", "vr-1"
    record.connector_id, record.connector_name = "conn-1", connector
    record.mime_type, record.extension = "text/plain", "txt"
    record.block_containers = BlocksContainer(
        blocks=[Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="hello")], block_groups=[]
    )
    return record


def _ingress(document: dict[str, object] | None) -> tuple[StageIngress, AsyncMock, AsyncMock]:
    coordinator, graph = AsyncMock(), AsyncMock()
    coordinator.on_external_done = AsyncMock(return_value=["job"])
    graph.get_document = AsyncMock(return_value=document)
    return StageIngress(coordinator, graph, MagicMock()), coordinator, graph


@pytest.mark.asyncio
async def test_the_revision_and_content_facts_describe_the_record() -> None:
    ingress, coordinator, _ = _ingress({"contentRev": "abc"})
    record = _record()
    assert await ingress.on_indexed(record, trigger="newRecord") == ["job"]
    stage, view = coordinator.on_external_done.await_args.args
    kwargs = coordinator.on_external_done.await_args.kwargs
    assert stage == "embed" and isinstance(view, RecordView)
    facts = content_facts(record.block_containers)
    assert (view.rev, view.text_digest, view.text_chars, view.tier) == ("abc", facts.text_digest, 5, ParseTier.LIGHT)
    assert kwargs == {
        "priority": Priority.INTERACTIVE, "trigger": "newRecord", "force": False,
        "keep_claim_on_publish_failure": True,
    }


@pytest.mark.asyncio
async def test_a_connector_record_is_bulk_priority() -> None:
    ingress, coordinator, _ = _ingress({"contentRev": "abc"})
    await ingress.on_indexed(_record(Connectors.GOOGLE_DRIVE), trigger="newRecord")
    assert coordinator.on_external_done.await_args.kwargs["priority"] is Priority.BULK


@pytest.mark.asyncio
async def test_a_missing_revision_is_derived_from_the_blocks_and_stored() -> None:
    ingress, coordinator, graph = _ingress({})
    record = _record()
    await ingress.on_indexed(record, trigger=None)
    rev = content_facts(record.block_containers).blocks_digest[:16]
    graph.update_node.assert_awaited_once_with("rec-1", "records", {"contentRev": rev})
    assert coordinator.on_external_done.await_args.args[1].rev == rev
    assert coordinator.on_external_done.await_args.kwargs["trigger"] == "index"


@pytest.mark.asyncio
async def test_a_forced_event_forces_the_stages_it_dispatches() -> None:
    ingress, coordinator, _ = _ingress({"contentRev": "abc"})
    ingress.mark_forced("rec-1")
    await ingress.on_indexed(_record(), trigger="reindexRecord")
    assert coordinator.on_external_done.await_args.kwargs["force"] is True
    ingress.clear_forced("rec-1")
    await ingress.on_indexed(_record(), trigger="reindexRecord")
    assert coordinator.on_external_done.await_args.kwargs["force"] is False


@pytest.mark.asyncio
async def test_a_deleted_record_is_an_error() -> None:
    ingress, _, _ = _ingress(None)
    with pytest.raises(LookupError):
        await ingress.on_indexed(_record(), trigger="newRecord")


@pytest.mark.asyncio
async def test_a_retry_re_runs_stages_for_the_current_revision() -> None:
    ingress, coordinator, _ = _ingress(None)
    coordinator.redrive_revision = AsyncMock(return_value=["vr-1:abc:classify@1"])
    assert await ingress.redrive("vr-1", "abc", ["classify"]) == ["vr-1:abc:classify@1"]
    coordinator.redrive_revision.assert_awaited_once_with(
        "vr-1", "abc", ["classify"], priority=Priority.INTERACTIVE, force=True
    )


@pytest.mark.asyncio
async def test_a_retry_without_a_revision_falls_back_to_a_reindex() -> None:
    ingress, coordinator, _ = _ingress(None)
    assert await ingress.redrive("vr-1", None, ["classify"]) is None
    assert await ingress.redrive(None, "abc", ["classify"]) is None
    coordinator.redrive_revision.assert_not_called()
