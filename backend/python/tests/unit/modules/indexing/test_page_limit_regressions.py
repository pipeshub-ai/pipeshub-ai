"""Page-limit behaviours that are easy to get wrong: what counts as changed content, retries after a refusal that failed part-way, count outages, status resets and dedup."""
from __future__ import annotations

import base64
import hashlib
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.indexing import page_limit
from app.modules.parsers.pdf.page_count import Unavailable, Unreadable
from app.utils.user_errors import (
    MAX_INDEXED_PDF_PAGES,
    PDF_PAGE_LIMIT_NOTE,
    pdf_over_page_limit,
)
from tests.unit.modules.indexing.test_page_limit import (
    BUFFER,
    COMPLETED,
    MD5,
    REV,
    VRID,
    World,
    searchable,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

NOT_SUPPORTED = ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
OLD_REV = "1" * 16  # the byte revision of the version that was indexed


# --- a connector that writes the source md5 before the event ----------------------------------------------------


OLD_MD5 = hashlib.md5(b"the version that was indexed").hexdigest()


@pytest.mark.asyncio
async def test_a_changed_drive_pdf_whose_sync_wrote_the_new_md5_is_refused() -> None:
    """Drive/GCS/Azure/Salesforce: the sync writes the source's md5 of the NEW file before the update event, so the
    record's md5 equals the downloaded bytes' md5 and says nothing about the index. ``indexedMd5`` does."""
    world = World(searchable(contentRev=OLD_REV, md5Checksum=MD5, indexedMd5=OLD_MD5))
    world.count(700)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    assert world.refused, "changed content kept: the stored md5 describes the new source file, not the indexed one"


@pytest.mark.asyncio
async def test_the_same_bytes_as_were_indexed_are_kept_even_after_a_blob_reembed_changed_the_revision() -> None:
    world = World(searchable(contentRev="a" * 16, md5Checksum=OLD_MD5, indexedMd5=MD5))
    world.count(700)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    assert not world.refused
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE


@pytest.mark.asyncio
async def test_a_record_indexed_before_the_field_existed_falls_back_to_the_older_rule_and_is_kept() -> None:
    """No ``indexedMd5``: the changed-at-source shape cannot be told from a blob re-embed, so the index is kept."""
    world = World(searchable(contentRev=OLD_REV, md5Checksum=MD5))
    assert "indexedMd5" not in world.graph.record
    world.count(700)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    assert not world.refused


def test_the_indexed_md5_decides_before_every_other_field() -> None:
    assert page_limit.content_state({"indexedMd5": MD5, "contentRev": "x", "md5Checksum": OLD_MD5}, REV, MD5) == "unchanged"
    assert page_limit.content_state({"indexedMd5": OLD_MD5, "contentRev": REV, "md5Checksum": MD5}, REV, MD5) == "changed"
    assert page_limit.content_state({"indexedMd5": "not an md5", "contentRev": "x"}, REV, MD5) == "uncertain"


# --- a refusal whose un-index fails part-way, then the retry ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_status", [None, "QUEUED"])
async def test_a_retry_after_a_partial_unindex_is_refused_again(retry_status: str | None) -> None:
    """A refusal whose un-index failed part-way leaves the record refused and not keep-eligible, so the retry refuses it again.
    ``unindex_record`` clears edges, mentions and fields before it detaches the virtual record, so a failure there leaves the
    virtual record and the stored content in place."""
    first = World(searchable(contentRev=OLD_REV, md5Checksum=hashlib.md5(b"old").hexdigest()))
    first.count(700)
    first.unindex.side_effect = RuntimeError("graph write failed in clear_classification")
    with patch("app.events.events.content_revision", return_value=REV), pytest.raises(RuntimeError):
        await first.run()
    after = dict(first.graph.record)
    assert after["indexingStatus"] == NOT_SUPPORTED and after["hasIndexedCopy"] is False
    assert after["virtualRecordId"] == VRID  # not detached yet
    if retry_status:
        after["indexingStatus"] = retry_status  # a user reindex of the refused file resets the status first
    again = World(after)
    again.count(700)
    with patch("app.events.events.content_revision", return_value=REV):
        await again.run()
    assert again.refused, f"kept on retry: {again.graph.record}"
    assert again.graph.record.get("hasIndexedCopy") is not True


# --- lane admission timeout during the count --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_parse_admission_timeout_is_not_an_unreadable_file() -> None:
    """A burst of PDFs makes lane admission time out. ParseAdmissionTimeout is RequeueWithoutAttempt: infrastructure, not
    the file. It must not be read as Unreadable (which re-indexes a keep-eligible file in full and skips the metric)."""
    from app.modules.parsers import parse_pool
    from app.modules.parsers.pdf.page_count import count_pdf_pages
    from app.services.messaging.consumer_concurrency import ParseAdmissionTimeout

    with patch.object(parse_pool, "submit", AsyncMock(side_effect=ParseAdmissionTimeout("parsing", 120))):
        try:
            outcome = await count_pdf_pages(b"%PDF-1.4", label="r")
        except ParseAdmissionTimeout:
            return  # re-raised for the consumer to re-queue: acceptable
    assert not isinstance(outcome, Unreadable), "admission timeout classified as an unreadable file"
    assert isinstance(outcome, Unavailable)


# --- status resets and live statuses --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_retry_of_an_in_progress_unchanged_file_is_kept_and_completed() -> None:
    world = World(searchable(indexingStatus="IN_PROGRESS", contentRev=REV))
    world.count(900)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    assert not world.refused
    assert world.graph.record["indexingStatus"] == COMPLETED


@pytest.mark.asyncio
async def test_a_failed_file_with_an_old_copy_forced_stays_failed() -> None:
    world = World(searchable(indexingStatus="FAILED", reason="boom"), extra_payload={"forceReindex": True})
    world.count(900)
    await world.run()
    assert not world.refused
    assert world.graph.record["indexingStatus"] == "FAILED"
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE


@pytest.mark.asyncio
async def test_auto_index_off_forced_without_the_flag_but_with_stored_content_keeps_its_status() -> None:
    record = searchable(indexingStatus="AUTO_INDEX_OFF")
    record.pop("hasIndexedCopy")
    world = World(record, extra_payload={"forceReindex": True})
    world.count(900)
    await world.run()
    assert not world.refused
    assert world.graph.record["indexingStatus"] == "AUTO_INDEX_OFF"


@pytest.mark.asyncio
async def test_a_kb_reindex_that_resets_to_not_started_and_loses_to_the_router_ends_completed() -> None:
    world = World(searchable(indexingStatus="NOT_STARTED", contentRev=REV))
    world.graph.lose_next_cas = True
    world.graph.cas_hook = lambda g: g.record.update(indexingStatus="QUEUED")
    world.count(501)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    assert world.graph.record["indexingStatus"] == COMPLETED
    world.unindex.assert_not_awaited()


# --- md5 forms ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (base64.b64encode(bytes.fromhex(MD5)).decode() + "\n", MD5),  # GCS value with a trailing newline
        (" " + MD5.upper() + " ", MD5),
        (bytearray(bytes.fromhex(MD5)), MD5),  # Azure content_md5 as the SDK returns it
        (base64.urlsafe_b64encode(bytes.fromhex("fb" * 16)).decode(), None),  # url-safe alphabet: not GCS, uncertain
        (base64.b64encode(b"x" * 18).decode(), None),  # 24 chars, 18 bytes: a version id, not an md5
        ("0" * 31, None),
        (12345, None),
    ],
)
def test_md5_forms(value: object, expected: str | None) -> None:
    assert page_limit.normalise_md5(value) == expected


@pytest.mark.asyncio
async def test_azure_base64_of_a_different_digest_on_a_pre_1_0_record_is_refused() -> None:
    other = base64.b64encode(hashlib.md5(b"other").digest()).decode()
    world = World(searchable(contentRev=None, md5Checksum=other))
    world.count(501)
    await world.run()
    assert world.refused


# --- count unavailable or unreadable, lost stored content -------------------------------------------------


@pytest.mark.asyncio
async def test_points_query_that_cannot_answer_keeps_the_index() -> None:
    world = World(searchable())
    world.processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value=None)
    world.processor.indexing_pipeline.orgs_holding_points = AsyncMock(side_effect=RuntimeError("qdrant down"))
    world.count(600)
    await world.run()
    assert not world.refused
    world.unindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_unavailable_count_on_a_reset_record_without_flag_and_stored_content_lost_is_not_deleted() -> None:
    record = searchable(indexingStatus="QUEUED")
    record.pop("hasIndexedCopy")
    world = World(record)
    world.processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value=None)
    world.count(Unavailable())
    world.inspected(None)
    await world.run()
    world.unindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_count_with_an_over_limit_inspection_on_a_flagged_file_never_refuses() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(Unreadable())
    world.inspected(5000)
    await world.run()
    assert not world.refused
    assert world.graph.record["pageCount"] == 5000


# --- the tombstone --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_tombstone_never_equals_any_byte_revision_even_with_no_revision() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(501)
    with patch("app.events.events.content_revision", return_value=None):
        await world.run()
    rev = world.graph.record["contentRev"]
    assert rev.startswith(page_limit.REFUSED_REV_PREFIX)
    assert world.graph.record["reason"] == pdf_over_page_limit(501)


# --- the retry after a partial un-index, once the md5 mismatch is gone ----------------------------------------


async def _partial_refusal(*, late: bool) -> dict:
    first = World(searchable(contentRev=OLD_REV, md5Checksum=hashlib.md5(b"old").hexdigest()))
    first.count(Unreadable() if late else 700)
    if late:
        first.inspected(700)
    first.unindex.side_effect = RuntimeError("graph write failed in clear_classification")
    with patch("app.events.events.content_revision", return_value=REV), pytest.raises(RuntimeError):
        await first.run()
    after = dict(first.graph.record)
    assert first.refused and after["virtualRecordId"] == VRID and after["hasIndexedCopy"] is False
    return after


@pytest.mark.asyncio
async def test_late_refusal_retry_after_dedup_rewrote_the_md5_is_refused_again() -> None:
    """The late site runs after _check_duplicate_by_md5, which writes md5Checksum = md5(new bytes) (events.py ~1087).
    The fake dedup here does not write, so the write is applied to the snapshot as the real one leaves it."""
    after = await _partial_refusal(late=True)
    after["md5Checksum"] = MD5
    again = World(after)
    again.count(Unreadable())
    again.inspected(700)
    with patch("app.events.events.content_revision", return_value=REV):
        await again.run()
    assert again.refused, f"not refused on retry: indexed={again.indexed} record={again.graph.record}"
    assert not again.indexed


@pytest.mark.asyncio
async def test_early_refusal_retry_after_a_connector_sync_rewrote_the_md5_is_refused_again() -> None:
    """Between the failed attempt and the retry, a Drive/GCS/Azure sync writes the source md5 of the new file."""
    after = await _partial_refusal(late=False)
    after["md5Checksum"] = MD5
    again = World(after)
    again.count(700)
    with patch("app.events.events.content_revision", return_value=REV):
        await again.run()
    assert again.refused, f"kept on retry: {again.graph.record}"
    assert again.graph.record.get("hasIndexedCopy") is not True


async def _empty() -> AsyncIterator[None]:
    return
    yield


@pytest.mark.asyncio
async def test_a_refusal_writes_no_indexed_md5() -> None:
    """This release only accepts the field: a refusal does not write it, not even a null."""
    world = World({"indexingStatus": "QUEUED"})
    world.count(700)
    await world.run()
    assert world.refused
    assert "indexedMd5" not in world.processor._mark_record.await_args.kwargs["extra_fields"]


# --- rules on the keep path and the refusal order ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_storage_error_on_the_fallback_path_keeps_the_index() -> None:
    """No ``hasIndexedCopy``, a VRID, and storage that cannot answer: nothing is deleted on uncertainty."""
    record = searchable(indexingStatus="QUEUED")
    record.pop("hasIndexedCopy")
    world = World(record)
    world.processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(side_effect=RuntimeError("503"))
    world.count(600)
    await world.run()
    assert not world.refused
    world.unindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_status_and_tombstone_are_written_before_the_unindex_starts() -> None:
    from unittest.mock import MagicMock

    world = World({"indexingStatus": "QUEUED"})
    world.count(600)
    world.unindex.side_effect = RuntimeError("graph write failed")
    order = MagicMock()
    order.attach_mock(world.processor._mark_record, "mark")
    order.attach_mock(world.unindex, "unindex")
    with pytest.raises(RuntimeError):
        await world.run()
    assert [call[0] for call in order.mock_calls] == ["mark", "unindex"]
    assert world.graph.record["contentRev"].startswith(page_limit.REFUSED_REV_PREFIX)


@pytest.mark.asyncio
async def test_the_event_fails_when_the_unindex_fails_so_it_is_retried() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(600)
    world.unindex.side_effect = RuntimeError("graph write failed")
    with pytest.raises(RuntimeError, match="graph write failed"):
        await world.run()
    assert not world.indexed


# --- a refusal's tombstone makes the record ineligible whatever else its fields say now -------------------------------


@pytest.mark.asyncio
async def test_a_refused_tombstone_is_never_keep_eligible_even_with_a_vrid_stored_content_and_equal_md5() -> None:
    world = World(searchable(contentRev=f"{page_limit.REFUSED_REV_PREFIX}{OLD_REV}", md5Checksum=MD5, indexedMd5=MD5))
    world.count(700)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    assert world.refused


# --- a twin over the limit is not reused when this file's page count is unknown ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("record", "count", "skips"),
    [
        ({"indexingStatus": "QUEUED"}, Unavailable(), True),
        ({"indexingStatus": "QUEUED"}, Unreadable(), True),
        (searchable(), Unreadable(), False),  # the inspection takes the count before dedup, so it is known by then
        ({"indexingStatus": "QUEUED"}, 12, False),
    ],
)
async def test_dedup_is_told_to_skip_over_limit_twins_only_when_the_count_is_unknown_and_nothing_is_kept(
    record: dict, count: object, skips: bool
) -> None:
    world = World(record)
    world.count(count)
    world.inspected(12)
    await world.run()
    assert world.dedup.await_args.kwargs["skip_over_limit_twins"] is skips


@pytest.mark.asyncio
async def test_dedup_does_not_reuse_a_twin_with_more_pages_than_may_be_indexed() -> None:
    from app.events.events import DedupDecision as _Decision
    from tests.unit.events.test_events import _make_event_processor

    ep, *_ = _make_event_processor()
    ep.graph_provider.update_node = AsyncMock(return_value=True)
    twin = {"_key": "twin", "indexingStatus": "COMPLETED", "pageCount": 600}
    ep._find_duplicate_records = AsyncMock(return_value=[twin])
    with patch("app.events.events.select_duplicate") as select:
        decision = await ep._check_duplicate_by_md5(
            BUFFER, {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1"}, md5=MD5, skip_over_limit_twins=True
        )
        select.assert_not_called()
    assert decision == _Decision(virtual_record_id=None, skip_indexing=False)

    with patch("app.events.events.select_duplicate", return_value=None) as select:
        await ep._check_duplicate_by_md5(BUFFER, {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1"}, md5=MD5)
        assert select.call_args.args[0] == [twin], "without the flag a twin is a candidate as before"


@pytest.mark.asyncio
async def test_dedup_does_not_reuse_a_noted_twin_when_the_count_is_unknown() -> None:
    """A twin the start-up audit noted has no page count (the audit writes only the note); it is still not a copy to reuse."""
    from app.events.events import DedupDecision as _Decision
    from tests.unit.events.test_events import _make_event_processor

    ep, *_ = _make_event_processor()
    ep.graph_provider.update_node = AsyncMock(return_value=True)
    twin = {
        "_key": "twin", "recordType": "FILE", "indexingStatus": COMPLETED, "virtualRecordId": "v-t",
        "md5Checksum": MD5, "indexingNote": PDF_PAGE_LIMIT_NOTE,
    }
    ep._find_duplicate_records = AsyncMock(return_value=[twin])
    decision = await ep._check_duplicate_by_md5(
        BUFFER, {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1"}, md5=MD5, skip_over_limit_twins=True
    )
    assert decision == _Decision(virtual_record_id=None, skip_indexing=False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("twin_fields", "reusable"),
    [
        ({}, False),  # indexed before counts were recorded: its size is not known, so it is not proven within the limit
        ({"indexingNote": "something else"}, False),
        ({"pageCount": 600}, False),
        ({"pageCount": MAX_INDEXED_PDF_PAGES + 1}, False),
        ({"pageCount": MAX_INDEXED_PDF_PAGES, "indexingNote": PDF_PAGE_LIMIT_NOTE}, False),
        ({"pageCount": MAX_INDEXED_PDF_PAGES}, True),
        ({"pageCount": 12}, True),
    ],
)
async def test_only_a_twin_proven_within_the_limit_is_a_candidate_when_the_count_is_unknown(
    twin_fields: dict, reusable: bool
) -> None:
    """A twin with no recorded count (indexed before counts were kept) must not carry an unchecked file past the limit."""
    from tests.unit.events.test_events import _make_event_processor

    ep, *_ = _make_event_processor()
    ep.graph_provider.update_node = AsyncMock(return_value=True)
    twin = {"_key": "twin", "recordType": "FILE", "indexingStatus": COMPLETED, **twin_fields}
    ep._find_duplicate_records = AsyncMock(return_value=[twin])
    with patch("app.events.events.select_duplicate", return_value=None) as select:
        await ep._check_duplicate_by_md5(
            BUFFER, {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1"}, md5=MD5, skip_over_limit_twins=True
        )
    if reusable:
        assert select.call_args.args[0] == [twin]
    else:
        select.assert_not_called()
