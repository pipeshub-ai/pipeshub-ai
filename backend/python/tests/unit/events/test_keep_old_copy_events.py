"""The swap decision of the event processor: which runs write a replacement copy and how the pending vrid is taken."""
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, ExtensionTypes, MimeTypes
from app.events.events import DedupDecision, EventProcessor
from app.exceptions.indexing_exceptions import IndexingError
from app.modules.indexing import keep_old_copy
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from tests.unit.attempt_start import attempt_start_via


def _processor() -> tuple[EventProcessor, MagicMock, AsyncMock]:
    processor = MagicMock()
    processor.indexing_pipeline = AsyncMock()
    processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="stored/path")
    processor.sink_orchestrator.blob_storage.get_reconciliation_metadata = AsyncMock(return_value={"blocks": 1})
    gp = AsyncMock()
    gp.update_node = AsyncMock(return_value=True)
    gp.write_attempt_start = attempt_start_via(gp)
    gp.update_node_fields_if_match = AsyncMock(return_value=True)
    return EventProcessor(MagicMock(), processor, gp, MagicMock()), processor, gp


def _payload(event_type: str, mime: str, ext: str, vrid: str | None = "X", force: bool = False) -> dict:
    payload = {
        "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": vrid, "version": 1, "connectorName": "",
        "extension": ext, "mimeType": mime, "recordName": "n", "buffer": b"hello",
    }
    if force:
        payload.update({"forceReindex": True, "refreshPaidOutputs": True})
    return {"payload": payload, "eventType": event_type}


async def _gen(*_a: object, **_k: object) -> AsyncIterator[PipelineEvent]:
    yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
    yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))


async def _drain(gen: AsyncIterator[PipelineEvent]) -> list[PipelineEvent]:
    return [e async for e in gen]


@pytest.fixture(autouse=True)
def _no_swap_left_over() -> Iterator[None]:
    yield
    keep_old_copy.end_swap()


@pytest.fixture
def swap_on(monkeypatch) -> None:
    monkeypatch.setattr(keep_old_copy, "swap_allowed", AsyncMock(return_value=True))


def _doc(**more: object) -> dict:
    return {"_key": "rec-1", "recordType": "FILE", "virtualRecordId": "X", "hasIndexedCopy": True, "indexingStatus": "COMPLETED", "orgId": "org-1", **more}


async def _run(ep: EventProcessor, processor: MagicMock, gp: AsyncMock, doc: dict, **payload: object) -> tuple[AsyncMock, MagicMock]:
    gp.get_document.return_value = doc
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    processor.process_pptx_document = MagicMock(side_effect=_gen)
    processor.process_md_document = MagicMock(side_effect=_gen)
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=DedupDecision()), \
         patch.object(ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock) as rewrite, \
         patch.object(ep, "_spawn_abandoned_vrid_cleanup") as cleanup:
        await _drain(ep.on_event(_payload(**payload)))
    return rewrite, cleanup


@pytest.mark.asyncio
async def test_an_update_of_an_unreconciled_type_leaves_the_record_on_its_old_copy_until_the_commit(swap_on) -> None:
    ep, processor, gp = _processor()
    rewrite, cleanup = await _run(ep, processor, gp, _doc(), event_type=EventTypes.REINDEX_RECORD.value,
                                  mime=MimeTypes.PPTX.value, ext=ExtensionTypes.PPTX.value)
    new_vrid = processor.process_pptx_document.call_args[1]["virtual_record_id"]
    assert new_vrid != "X"
    # the pending vrid is written by a compare-and-set that names the vrid replaced, and virtualRecordId is not written
    gp.update_node_fields_if_match.assert_awaited_once_with(
        "rec-1", "records", {"pendingVirtualRecordId": new_vrid}, {"virtualRecordId": "X", "pendingVirtualRecordId": None},
    )
    assert not any("virtualRecordId" in (c.args[2] if len(c.args) > 2 else {}) for c in gp.update_node.await_args_list)
    # the old copy is not removed before the parse
    rewrite.assert_not_called()
    cleanup.assert_not_called()


@pytest.mark.asyncio
async def test_with_the_swap_off_the_record_moves_and_the_old_copy_goes_first_as_it_always_did(monkeypatch) -> None:
    monkeypatch.setattr(keep_old_copy, "swap_allowed", AsyncMock(return_value=False))
    ep, processor, gp = _processor()
    rewrite, cleanup = await _run(ep, processor, gp, _doc(), event_type=EventTypes.REINDEX_RECORD.value,
                                  mime=MimeTypes.PPTX.value, ext=ExtensionTypes.PPTX.value)
    gp.update_node_fields_if_match.assert_not_called()
    rewrite.assert_awaited_once_with("X")
    cleanup.assert_called_once_with("org-1", "X")


@pytest.mark.asyncio
async def test_a_record_without_an_indexed_copy_has_nothing_to_keep(swap_on) -> None:
    ep, processor, gp = _processor()
    await _run(ep, processor, gp, _doc(hasIndexedCopy=False, indexingStatus="NOT_STARTED", virtualRecordId=None),
               event_type=EventTypes.NEW_RECORD.value, mime=MimeTypes.PPTX.value, ext=ExtensionTypes.PPTX.value, vrid=None)
    gp.update_node_fields_if_match.assert_not_called()


@pytest.mark.asyncio
async def test_a_forced_reindex_of_a_reconciled_record_writes_its_own_copy(swap_on) -> None:
    ep, processor, gp = _processor()
    await _run(ep, processor, gp, _doc(recordType="FILE"), event_type=EventTypes.REINDEX_RECORD.value,
               mime=MimeTypes.MARKDOWN.value, ext=ExtensionTypes.MD.value, force=True)
    pending = gp.update_node_fields_if_match.await_args
    assert pending is not None and pending.args[3] == {"virtualRecordId": "X", "pendingVirtualRecordId": None}
    assert pending.args[2]["pendingVirtualRecordId"] != "X"
    run = keep_old_copy.current_swap()
    assert run is not None and run.forced and run.old_vrid == "X"


@pytest.mark.asyncio
async def test_a_lost_pending_write_fails_the_event_so_it_is_retried(swap_on) -> None:
    ep, processor, gp = _processor()
    gp.update_node_fields_if_match = AsyncMock(return_value=False)
    with pytest.raises(IndexingError):
        await _run(ep, processor, gp, _doc(), event_type=EventTypes.REINDEX_RECORD.value,
                   mime=MimeTypes.PPTX.value, ext=ExtensionTypes.PPTX.value)


@pytest.mark.asyncio
async def test_an_earlier_pending_copy_is_released_only_after_the_new_pending_write_won(swap_on) -> None:
    ep, processor, gp = _processor()
    order = []
    gp.update_node_fields_if_match = AsyncMock(side_effect=lambda *a, **k: order.append("cas") or True)
    gp.get_document.return_value = _doc(pendingVirtualRecordId="P")
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    processor.process_pptx_document = MagicMock(side_effect=_gen)
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=DedupDecision()), \
         patch.object(ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock, side_effect=lambda v: order.append(f"release {v}")), \
         patch.object(ep, "_spawn_abandoned_vrid_cleanup"):
        await _drain(ep.on_event(_payload(EventTypes.REINDEX_RECORD.value, MimeTypes.PPTX.value, ExtensionTypes.PPTX.value)))
    assert order == ["cas", "release P"]
    assert gp.update_node_fields_if_match.await_args.args[3] == {"virtualRecordId": "X", "pendingVirtualRecordId": "P"}


@pytest.mark.asyncio
async def test_a_reconciled_record_with_stored_metadata_is_diffed_in_place(swap_on) -> None:
    ep, processor, gp = _processor()
    await _run(ep, processor, gp, _doc(), event_type=EventTypes.UPDATE_RECORD.value,
               mime=MimeTypes.MARKDOWN.value, ext=ExtensionTypes.MD.value)
    gp.update_node_fields_if_match.assert_not_called()  # nothing is replaced: the diff keeps the copy it has


@pytest.mark.asyncio
async def test_a_reconciled_record_with_no_stored_metadata_writes_a_new_copy_instead_of_purging_the_old(swap_on) -> None:
    ep, processor, gp = _processor()
    processor.sink_orchestrator.blob_storage.get_reconciliation_metadata = AsyncMock(return_value=None)
    await _run(ep, processor, gp, _doc(), event_type=EventTypes.UPDATE_RECORD.value,
               mime=MimeTypes.MARKDOWN.value, ext=ExtensionTypes.MD.value)
    pending = gp.update_node_fields_if_match.await_args
    assert pending is not None and pending.args[3] == {"virtualRecordId": "X", "pendingVirtualRecordId": None}
    kwargs = processor.process_md_document.call_args[1]
    assert kwargs["virtual_record_id"] != "X" and kwargs["prev_virtual_record_id"] == "X"


@pytest.mark.asyncio
async def test_an_unreadable_metadata_store_fails_the_event_instead_of_reading_as_no_metadata(swap_on) -> None:
    ep, processor, gp = _processor()
    processor.sink_orchestrator.blob_storage.get_reconciliation_metadata = AsyncMock(side_effect=RuntimeError("down"))
    with pytest.raises(RuntimeError):
        await _run(ep, processor, gp, _doc(), event_type=EventTypes.UPDATE_RECORD.value,
                   mime=MimeTypes.MARKDOWN.value, ext=ExtensionTypes.MD.value)
    gp.update_node_fields_if_match.assert_not_called()


@pytest.mark.asyncio
async def test_a_forced_run_never_rebuilds_a_shared_copy_in_place_and_leaves_the_twin_its_points(swap_on) -> None:
    ep, processor, gp = _processor()
    gp.get_document.return_value = _doc()
    gp.find_duplicates = AsyncMock(return_value=[])
    twin = {"_key": "twin", "virtualRecordId": "X", "indexingStatus": "COMPLETED", "md5Checksum": "m", "orgId": "org-1"}
    from unittest.mock import MagicMock as _M

    from app.events.dedup import DuplicateMatch

    match = DuplicateMatch(record=twin, same_collection=True, is_processed=True)
    with patch.object(ep, "_find_duplicate_records", new_callable=AsyncMock, return_value=[twin]), \
         patch("app.events.events.select_duplicate", _M(return_value=match)), \
         patch.object(ep, "_resolve_write_collection", return_value="c1"):
        decision = await ep._check_duplicate_by_md5(b"hello", _doc(), force_index=True)
    assert decision.rebuild_shared_vrid is False and not decision.skip_indexing


async def _reuse_in_other_collection(ep: EventProcessor, gp: AsyncMock, doc: dict) -> DedupDecision:
    from app.events.dedup import DuplicateMatch

    twin = {"_key": "twin", "virtualRecordId": "T", "indexingStatus": "COMPLETED", "md5Checksum": "m", "orgId": "org-1"}
    gp.get_document.return_value = doc
    ep._copy_twin_edges = AsyncMock()
    ep._vrid_has_stored_content = AsyncMock(return_value=True)
    return await ep._reuse_finished_duplicate(doc, DuplicateMatch(record=twin, same_collection=False, is_processed=True))


@pytest.mark.asyncio
async def test_a_twin_in_another_collection_is_not_attached_before_the_run_when_the_old_copy_is_kept(swap_on) -> None:
    """The eager identity write is what dropped the old copy: the record would name the twin's vrid with none of its own points."""
    ep, _processor_mock, gp = _processor()
    decision = await _reuse_in_other_collection(ep, gp, _doc())
    assert decision.virtual_record_id is None and not decision.skip_indexing
    assert not any("virtualRecordId" in (c.args[2] if len(c.args) > 2 else {}) for c in gp.update_node.await_args_list)


@pytest.mark.asyncio
async def test_a_twin_in_another_collection_is_attached_as_before_when_the_old_copy_is_not_kept(monkeypatch) -> None:
    monkeypatch.setattr(keep_old_copy, "swap_allowed", AsyncMock(return_value=False))
    ep, _processor_mock, gp = _processor()
    gp.update_node = AsyncMock(return_value=True)
    decision = await _reuse_in_other_collection(ep, gp, _doc())
    assert decision.virtual_record_id == "T"


@pytest.mark.asyncio
async def test_a_redelivery_resumes_into_the_pending_copy_not_into_the_vrid_the_record_names(swap_on) -> None:
    """the marker names the copy the interrupted run was writing, which the record only has as its pending vrid."""
    from app.modules.indexing.embed_resume import DELIVERY_ID_KEY

    ep, processor, gp = _processor()
    seen: dict = {}

    def spy(marker, **kwargs) -> bool:
        seen.update(kwargs)
        return False

    doc = _doc(pendingVirtualRecordId="Y", embedVrid="Y", embedRev="rev", embedDeliveryId="d1", embedDigest="dg")
    gp.get_document.return_value = doc
    gp.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    processor.process_pptx_document = MagicMock(side_effect=_gen)
    data = _payload(EventTypes.REINDEX_RECORD.value, MimeTypes.PPTX.value, ExtensionTypes.PPTX.value)
    data["payload"][DELIVERY_ID_KEY] = "d1"
    with patch("app.events.events.may_resume", spy), \
         patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=DedupDecision()), \
         patch.object(ep, "_spawn_abandoned_vrid_cleanup"), patch.object(ep, "_rewrite_or_delete_vrid_vectors", new_callable=AsyncMock):
        await _drain(ep.on_event(data))
    assert seen["node_vrid"] == "Y"  # not "X", which the record names


@pytest.mark.asyncio
async def test_a_record_that_is_replacing_its_copy_has_nothing_to_parse_now() -> None:
    """a parse-now request for a record that serves an indexed copy while its replacement is written is refused as redundant, so the
    parse-now marker (written before the commit, on a parse-now run only) is never written under the revision of a swap in flight."""
    from app.services.messaging.kafka.handlers.record import RecordEventHandler

    handler = RecordEventHandler.__new__(RecordEventHandler)
    record = {"_key": "r1", "orgId": "o1", "virtualRecordId": "X", "pendingVirtualRecordId": "Y", "hasIndexedCopy": True,
              "indexingStatus": "IN_PROGRESS", "contentRev": "r2"}
    refusal = await handler._parse_now_refusal(record, {"recordId": "r1", "orgId": "o1"})
    assert refusal == ("the record is already readable", True)


@pytest.mark.asyncio
async def test_an_empty_parse_result_commits_through_the_swap_and_does_not_delete_the_old_copy_first() -> None:
    """The service path: while a replacement is pending the old copy must stay until the empty result commits."""
    from app.models.blocks import BlocksContainer

    ep, processor, gp = _processor()
    ep.parsing_client = MagicMock(circuit_open=False)
    ep.parsing_client.parse = AsyncMock(return_value=MagicMock(block_container=BlocksContainer(blocks=[], block_groups=[]), provider_used=MagicMock(value="t")))
    ep.extraction_client = MagicMock()
    ep.sink_orchestrator = MagicMock()
    ep.sink_orchestrator.swap.commit_document = AsyncMock(return_value=True)
    ep._drop_the_previous_vectors_of_an_emptied_record = AsyncMock()
    gp.get_document.return_value = _doc()
    keep_old_copy.begin_swap(keep_old_copy.SwapRun("rec-1", "org-1", "X", "Y"))
    with patch.dict("os.environ", {"USE_PARSING_SERVICE": "true"}):
        events = [e async for e in ep._orchestrate_via_services(
            record_id="rec-1", org_id="org-1", virtual_record_id="Y", record_name="a.txt", mime_type="text/plain", extension="txt",
            event_type=EventTypes.REINDEX_RECORD.value, prev_virtual_record_id="X", file_content=b"hello",
        )]
    assert events
    ep.sink_orchestrator.swap.commit_document.assert_awaited_once()
    ep._drop_the_previous_vectors_of_an_emptied_record.assert_not_awaited()
    fields = ep.sink_orchestrator.swap.commit_document.await_args.args[2]
    assert fields["indexingStatus"] == "EMPTY"
