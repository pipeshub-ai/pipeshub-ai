"""The PDF page limit, through the real ``EventProcessor.on_event`` with a fake graph.

Only the page count (``count_pdf_pages``), the whole-file inspection and the un-index routine are replaced, so the
early and late call sites, the keep rules and the writes are the production ones.
"""
from __future__ import annotations

import base64
import hashlib
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.plugins.telemetry.modules import indexing_metrics
from app.config.constants.arangodb import ProgressStatus
from app.events.events import DedupDecision
from app.exceptions.indexing_exceptions import PasswordProtectedError
from app.modules.indexing import page_limit
from app.modules.parsers.pdf.page_count import Unavailable, Unreadable
from app.modules.parsers.pdf.untrusted_pdf import InspectedPdf
from app.utils.user_errors import (
    MAX_INDEXED_PDF_PAGES,
    PDF_PAGE_LIMIT_NOTE,
    pdf_over_page_limit,
)
from tests.unit.events.test_events import (
    _drain,
    _make_event_payload,
    _make_event_processor,
    _mock_processor_gen,
    _pdf_inspection,
)

COMPLETED = ProgressStatus.COMPLETED.value
BUFFER = b"%PDF-1.4 not a real file"
MD5 = hashlib.md5(BUFFER).hexdigest()
REV = "9" * 16
VRID = "v-1"


class FakeGraph:
    """Records by key with the reads and conditional writes the page limit uses. A ``None`` removes a field."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1", **record}
        self.cas_calls: list[tuple] = []
        self.lose_next_cas = False
        self.cas_hook = None
        self.hook_on_every_write = False

    async def get_document(self, key: str, collection: str, **_kw: object) -> dict[str, Any]:
        return dict(self.record)

    async def update_node(self, key: str, collection: str, fields: dict[str, Any], *_a: object, **_kw: object) -> bool:
        self._merge(fields)
        return True

    def _merge(self, fields: dict[str, Any]) -> None:
        for name, value in fields.items():
            if value is None:
                self.record.pop(name, None)
            else:
                self.record[name] = value

    async def update_node_fields_if_match(
        self, key: str, collection: str, updates: dict[str, Any], expected: dict[str, Any], *_a: object,
        unset: tuple = (), **_kw: object,
    ) -> bool:
        status_write = "indexingStatus" in updates
        if status_write:
            self.cas_calls.append((expected.get("indexingStatus"), updates["indexingStatus"]))
        if self.cas_hook is not None and (status_write or self.hook_on_every_write):
            self.cas_hook(self)
        if status_write and self.lose_next_cas:
            self.lose_next_cas = False
            return False
        if any(self.record.get(name) != value for name, value in expected.items()):
            return False
        self._merge(updates)
        for name in unset:
            self.record.pop(name, None)
        return True

    async def compare_and_set_indexing_status(self, ids: list[str], expected: str, new: str, *_a: object) -> list[str]:
        self.cas_calls.append((expected, new))
        if self.cas_hook is not None:
            self.cas_hook(self)
        if self.lose_next_cas:
            self.lose_next_cas = False
            return []
        if self.record.get("indexingStatus") != expected:
            return []
        self.record["indexingStatus"] = new
        return list(ids)


class World:
    def __init__(self, record: dict[str, Any], *, buffer: bytes = BUFFER, extra_payload: dict[str, Any] | None = None) -> None:
        self.graph = FakeGraph(record)
        self.ep, _logger, self.processor, self.gp = _make_event_processor()
        for name in ("get_document", "update_node", "update_node_fields_if_match", "compare_and_set_indexing_status"):
            setattr(self.gp, name, getattr(self.graph, name))
        self.processor._mark_record = AsyncMock(side_effect=self._mark_record)
        self.processor.process_pdf_with_docling = MagicMock(side_effect=_mock_processor_gen)
        self.processor.process_pdf_with_pdf_plumber = MagicMock(side_effect=_mock_processor_gen)
        self.processor.process_pdf_document_with_ocr = MagicMock(side_effect=_mock_processor_gen)
        self.processor.indexing_pipeline.orgs_holding_points = AsyncMock(return_value={"org-1"})
        self.buffer = buffer
        self.extra_payload = extra_payload or {}
        self.stack = ExitStack()
        self.unindex = self.stack.enter_context(patch("app.modules.indexing.unindex.unindex_record", new_callable=AsyncMock))
        self.dedup = self.stack.enter_context(patch.object(
            self.ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
            return_value=DedupDecision(virtual_record_id=None, skip_indexing=False),
        ))
        self.resume = self.stack.enter_context(patch("app.events.events.may_resume", return_value=False))
        self.stack.enter_context(patch.dict("os.environ", {"ENABLE_PDFPLUMBER_PROCESSOR": "false"}))
        self.notified = self.stack.enter_context(patch("app.events.events.notify_record_indexed", new_callable=AsyncMock))
        self.inspect = None

    async def _mark_record(self, record_id: str, status: ProgressStatus, reason: str | None = None, extra_fields=None) -> None:
        fields = {"indexingStatus": status.value, "reason": reason, **(extra_fields or {})}
        self.graph._merge(fields)

    def count(self, outcome: object) -> AsyncMock:
        return self.stack.enter_context(patch(
            "app.modules.parsers.pdf.page_count.count_pdf_pages", new_callable=AsyncMock,
            **({"side_effect": outcome} if isinstance(outcome, BaseException) else {"return_value": outcome}),
        ))

    def inspected(self, pages: int | None = None) -> AsyncMock:
        self.inspect = self.stack.enter_context(_pdf_inspection(page_count=pages))
        return self.inspect

    async def run(self, event_type: str | None = None) -> list:
        if self.inspect is None:
            self.inspected(None)
        event = _make_event_payload(extension="pdf", buffer=self.buffer, event_type=event_type)
        event["payload"].update(self.extra_payload)
        try:
            return await _drain(self.ep.on_event(event))
        finally:
            self.stack.close()

    @property
    def refused(self) -> bool:
        return self.processor._mark_record.await_count > 0

    @property
    def indexed(self) -> bool:
        return any(
            getattr(self.processor, name).called
            for name in ("process_pdf_with_docling", "process_pdf_with_pdf_plumber", "process_pdf_document_with_ocr")
        )


def searchable(**fields: object) -> dict[str, Any]:
    return {
        "indexingStatus": COMPLETED, "hasIndexedCopy": True, "virtualRecordId": VRID, "contentRev": "other",
        "md5Checksum": MD5, **fields,
    }


# --- thresholds and ordering ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_500_pages_proceeds_and_the_parse_records_the_page_count() -> None:
    """The count of a file that is parsed is recorded by the parse, tied to the revision it was taken from; the early count
    only decides."""
    world = World({"indexingStatus": "QUEUED"})
    world.count(MAX_INDEXED_PDF_PAGES)
    world.inspected(MAX_INDEXED_PDF_PAGES)
    await world.run()
    assert world.indexed and not world.refused
    assert world.graph.record["pageCount"] == MAX_INDEXED_PDF_PAGES
    world.dedup.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_new_501_page_pdf_is_refused_before_inspection_dedup_or_resume() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(501)
    inspect = world.inspected(501)
    await world.run()

    assert world.refused
    _, kwargs = world.processor._mark_record.await_args
    assert kwargs["reason"] == pdf_over_page_limit(501)
    assert world.processor._mark_record.await_args.args[1] is ProgressStatus.FILE_TYPE_NOT_SUPPORTED
    inspect.assert_not_awaited()
    world.dedup.assert_not_awaited()
    world.resume.assert_not_called()
    world.unindex.assert_awaited_once()
    assert not world.indexed
    assert "pageCount" not in world.graph.record, "a refused record carries no count: its revision is a tombstone"


@pytest.mark.asyncio
async def test_a_refusal_writes_the_tombstone_and_clears_the_indexed_copy_flag_in_one_write() -> None:
    world = World({"indexingStatus": "QUEUED", "contentRev": "old"})
    world.count(600)
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    extra = world.processor._mark_record.await_args.kwargs["extra_fields"]
    assert extra["contentRev"] == f"{page_limit.REFUSED_REV_PREFIX}{REV}"
    assert extra["hasIndexedCopy"] is False
    assert extra["pageCount"] is None


@pytest.mark.asyncio
async def test_md5_is_taken_once_and_handed_to_dedup() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(10)
    with patch.object(world.ep, "_hash_for_dedup", wraps=world.ep._hash_for_dedup) as hashed:
        await world.run()
    assert hashed.call_count == 1
    assert world.dedup.await_args.kwargs["md5"] == MD5


@pytest.mark.asyncio
async def test_dedup_hand_off_skips_a_second_hash() -> None:
    ep, *_ = _make_event_processor()
    ep.graph_provider.update_node = AsyncMock(return_value=True)
    ep._find_duplicate_records = AsyncMock(return_value=[])
    with patch.object(ep, "_hash_for_dedup") as hashed:
        await ep._check_duplicate_by_md5(BUFFER, {"_key": "rec-1", "recordType": "FILE"}, md5="a" * 32)
    hashed.assert_not_called()


# --- record shapes ------------------------------------------------------------------------------------------------------


def _gcs(hexdigest: str) -> str:
    return base64.b64encode(bytes.fromhex(hexdigest)).decode()


OTHER = hashlib.md5(b"another file").hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shape", "record", "kept"),
    [
        ("current", searchable(contentRev=REV, md5Checksum=None), True),
        ("blob re-embedded: other revision, md5 equal", searchable(contentRev="a" * 16), True),
        ("pre-1.0: no revision, md5 equal", searchable(contentRev=None), True),
        ("pre-1.0: no revision, md5 different", searchable(contentRev=None, md5Checksum=OTHER), False),
        ("base64 of the same digest", searchable(md5Checksum=_gcs(MD5)), True),
        ("base64 of a different digest", searchable(md5Checksum=_gcs(OTHER)), False),
        ("raw 16 bytes of the same digest", searchable(md5Checksum=bytes.fromhex(MD5)), True),
        ("upper-case hex of the same digest", searchable(md5Checksum=MD5.upper()), True),
        ("not an md5 (an etag)", searchable(md5Checksum='"0x8D9F2E7B1A2C3D4"'), True),
        ("neither field", searchable(md5Checksum=None, contentRev=None), True),
    ],
)
async def test_each_record_shape_over_the_limit(shape: str, record: dict, kept: bool) -> None:
    world = World(record)
    world.count(501)
    # The record's own revision is the revision of these bytes only for the "current" shape.
    with patch("app.events.events.content_revision", return_value=REV):
        await world.run()
    if kept:
        assert not world.refused, shape
        world.unindex.assert_not_awaited()
        assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
        assert world.graph.record["indexingStatus"] == COMPLETED
    else:
        assert world.refused, shape
        world.unindex.assert_awaited_once()
    assert not world.indexed
    world.dedup.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_new_record_without_a_searchable_index_is_refused_even_with_a_vrid() -> None:
    world = World({"indexingStatus": "FAILED", "virtualRecordId": VRID})
    world.count(501)
    world.processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value=None)
    await world.run()
    assert world.refused


@pytest.mark.asyncio
async def test_a_refused_record_forced_again_is_refused_again_and_never_kept() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(501)
    await world.run()
    assert not page_limit.is_searchable_record(world.graph.record)
    # The same bytes forced again: the record is the refused one (status written, flag cleared, tombstone revision).
    again = World(dict(world.graph.record, indexingStatus="QUEUED"))
    again.count(501)
    await again.run()
    assert again.refused
    assert again.graph.record["indexingStatus"] != COMPLETED


# --- status resets -------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["QUEUED", "NOT_STARTED"])
async def test_a_reset_status_of_an_unchanged_file_is_restored_to_completed(status: str) -> None:
    world = World(searchable(indexingStatus=status))
    world.count(600)
    await world.run()
    assert world.graph.record["indexingStatus"] == COMPLETED
    assert world.graph.cas_calls == [(status, COMPLETED)]
    world.unindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_completed_snapshot_is_kept_without_a_status_write() -> None:
    world = World(searchable())
    world.count(600)
    await world.run()
    assert world.graph.cas_calls == []
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    assert world.graph.record["pageCount"] == 600


@pytest.mark.asyncio
async def test_a_record_without_the_flag_but_with_a_vrid_and_stored_content_is_kept_and_flagged() -> None:
    record = searchable(indexingStatus="QUEUED")
    record.pop("hasIndexedCopy")
    world = World(record)
    world.count(600)
    await world.run()
    assert not world.refused
    assert world.graph.record["hasIndexedCopy"] is True
    assert world.graph.record["indexingStatus"] == COMPLETED


@pytest.mark.asyncio
async def test_a_lost_cas_leaves_the_status_to_its_writer() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.graph.lose_next_cas = True
    world.graph.cas_hook = lambda g: g.record.update(indexingStatus="IN_PROGRESS")
    world.count(600)
    await world.run()
    assert world.graph.record["indexingStatus"] == "IN_PROGRESS"
    assert len(world.graph.cas_calls) == 1


@pytest.mark.asyncio
async def test_a_lost_cas_that_finds_queued_is_retried_once_and_ends_completed() -> None:
    world = World(searchable(indexingStatus="NOT_STARTED"))
    world.graph.lose_next_cas = True
    world.graph.cas_hook = lambda g: g.record.update(indexingStatus="QUEUED")
    world.count(600)
    await world.run()
    assert world.graph.record["indexingStatus"] == COMPLETED
    assert world.graph.cas_calls == [("NOT_STARTED", COMPLETED), ("QUEUED", COMPLETED)]


@pytest.mark.asyncio
async def test_a_failed_snapshot_keeps_its_status_and_reason_and_only_gets_the_note() -> None:
    world = World(searchable(indexingStatus="FAILED", reason="boom"))
    world.count(600)
    await world.run()
    assert world.graph.record["indexingStatus"] == "FAILED"
    assert world.graph.record["reason"] == "boom"
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    assert world.graph.cas_calls == []


@pytest.mark.asyncio
async def test_auto_index_off_with_a_forced_reindex_and_a_copy_is_kept_and_completed() -> None:
    world = World(searchable(indexingStatus="AUTO_INDEX_OFF"), extra_payload={"forceReindex": True})
    world.count(600)
    await world.run()
    assert world.graph.record["indexingStatus"] == COMPLETED


@pytest.mark.asyncio
async def test_auto_index_off_without_a_forced_reindex_keeps_its_status_and_gets_only_the_note() -> None:
    world = World(searchable(indexingStatus="AUTO_INDEX_OFF", reason="CONNECTOR_OFF"))
    world.count(600)
    await world.run()
    assert world.graph.record["indexingStatus"] == "AUTO_INDEX_OFF"
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    assert world.graph.record["reason"] == "CONNECTOR_OFF"


@pytest.mark.asyncio
async def test_after_an_unavailable_keep_nothing_is_left_queued_for_the_stranded_sweep() -> None:
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(Unavailable())
    world.inspected(900)
    await world.run()
    assert world.graph.record["indexingStatus"] == COMPLETED
    queued = [r for r in [world.graph.record] if r["indexingStatus"] == "QUEUED"]
    assert queued == []


# --- count outcomes ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unavailable_count_on_a_searchable_file_is_decided_by_the_inspection_before_any_write() -> None:
    """The index is kept when the inspection finds the file over the limit, and nothing was written ahead of it."""
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(Unavailable())
    writes_before_inspection: list[dict] = []
    inspect = world.inspected(900)
    inspect.side_effect = lambda content, **_kw: (
        writes_before_inspection.append(dict(world.graph.record)),
        InspectedPdf(content, needs_ocr=False, repaired=False, page_count=900),
    )[1]
    with patch.object(indexing_metrics.PAGE_COUNT_UNAVAILABLE, "inc") as inc:
        await world.run()
    inc.assert_called_once()
    inspect.assert_awaited_once()
    assert writes_before_inspection[0]["indexingStatus"] == "QUEUED", "the record was written before the inspection"
    world.unindex.assert_not_awaited()
    assert world.graph.record["indexingStatus"] == COMPLETED
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    assert not world.indexed


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [Unavailable(), Unreadable()])
async def test_a_count_outage_never_completes_a_reindex_of_a_file_within_the_limit(count: object) -> None:
    """A requested reindex of an unchanged 20-page file during a count outage is indexed, not acknowledged."""
    world = World(searchable(indexingStatus="QUEUED"))
    world.count(count)
    world.inspected(20)
    await world.run()
    assert world.indexed and not world.refused
    assert world.graph.record["pageCount"] == 20


@pytest.mark.asyncio
async def test_an_unavailable_count_on_a_new_file_is_decided_by_the_inspection() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(Unavailable())
    inspect = world.inspected(700)
    await world.run()
    inspect.assert_awaited_once()
    assert world.refused
    assert not world.indexed


@pytest.mark.asyncio
async def test_an_unreadable_count_on_a_keep_eligible_file_keeps_the_index_and_parses_nothing() -> None:
    """The late site keeps exactly as the early site does: the note, the count, the status restored, no parse."""
    world = World(searchable())
    world.count(Unreadable())
    world.inspected(600)
    await world.run()
    assert not world.refused
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE
    assert world.graph.record["pageCount"] == 600
    assert world.graph.record["indexingStatus"] == "COMPLETED"
    assert not world.indexed
    world.unindex.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_unreadable_count_on_a_new_file_is_refused_at_the_late_site() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(Unreadable())
    world.inspected(600)
    await world.run()
    assert world.refused
    world.unindex.assert_awaited_once()
    assert not world.indexed


@pytest.mark.asyncio
async def test_an_unreadable_count_with_a_short_inspection_indexes_as_before() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(Unreadable())
    world.inspected(12)
    await world.run()
    assert world.indexed and not world.refused
    assert world.graph.record["pageCount"] == 12


@pytest.mark.asyncio
async def test_an_inspected_count_of_zero_never_writes_a_page_count() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(Unreadable())
    world.inspected(0)
    await world.run()
    assert "pageCount" not in world.graph.record
    assert world.indexed


@pytest.mark.asyncio
async def test_a_password_protected_file_still_reaches_the_inspection_that_reports_it() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.count(PasswordProtectedError("locked"))
    world.inspected(None)
    world.inspect.side_effect = PasswordProtectedError("locked")
    with pytest.raises(PasswordProtectedError):
        await world.run()
    assert not world.refused


@pytest.mark.asyncio
async def test_store_only_and_non_pdf_files_are_not_counted() -> None:
    world = World({"indexingStatus": "QUEUED"})
    counted = world.count(900)
    with patch("app.events.events.store_only_active", return_value=True):
        await world.run()
    counted.assert_not_awaited()
    assert not world.refused

    other = World({"indexingStatus": "QUEUED"}, buffer=b"hello")
    counted = other.count(900)
    other.stack.enter_context(patch.object(other.ep, "_dispatch_pdf_binary"))
    event = _make_event_payload(extension="txt", buffer=b"hello")
    try:
        await _drain(other.ep.on_event(event))
    except Exception:
        pass
    finally:
        other.stack.close()
    counted.assert_not_awaited()


# --- stored content gone -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stored_content_gone_but_points_remain_keeps_the_index() -> None:
    world = World(searchable())
    world.processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value=None)
    world.count(600)
    await world.run()
    assert not world.refused
    world.unindex.assert_not_awaited()
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE


@pytest.mark.asyncio
async def test_stored_content_and_points_both_gone_refuses() -> None:
    world = World(searchable())
    world.processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value=None)
    world.processor.indexing_pipeline.orgs_holding_points = AsyncMock(return_value=set())
    world.count(600)
    await world.run()
    assert world.refused
    world.unindex.assert_awaited_once()


@pytest.mark.asyncio
async def test_storage_that_cannot_answer_keeps_the_index() -> None:
    world = World(searchable())
    world.processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(side_effect=RuntimeError("503"))
    world.count(600)
    await world.run()
    assert not world.refused
    assert world.graph.record["indexingNote"] == PDF_PAGE_LIMIT_NOTE


@pytest.mark.asyncio
async def test_a_flagged_record_with_no_vrid_is_kept_without_asking_storage() -> None:
    world = World(searchable(virtualRecordId=None))
    blob = world.processor.sink_orchestrator.blob_storage
    blob.get_actual_content_path = AsyncMock()
    world.count(600)
    await world.run()
    assert not world.refused
    blob.get_actual_content_path.assert_not_awaited()
    world.processor.indexing_pipeline.orgs_holding_points.assert_not_awaited()


# --- the note ------------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_kept_file_that_comes_back_short_has_its_note_cleared_and_a_foreign_note_is_left() -> None:
    world = World(searchable(indexingNote=PDF_PAGE_LIMIT_NOTE))
    world.count(300)
    world.inspected(300)
    await world.run()
    assert "indexingNote" not in world.graph.record
    assert world.graph.record["pageCount"] == 300

    foreign = World(searchable(indexingNote="something else"))
    foreign.count(300)
    await foreign.run()
    assert foreign.graph.record["indexingNote"] == "something else"


@pytest.mark.asyncio
async def test_a_refusal_clears_the_page_limit_note_only() -> None:
    world = World(searchable(indexingNote=PDF_PAGE_LIMIT_NOTE, md5Checksum=OTHER))
    world.count(700)
    await world.run()
    assert world.refused
    assert "indexingNote" not in world.graph.record


# --- dedup and resume -----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_over_limit_twin_is_refused_and_never_reuses_a_finished_copy() -> None:
    world = World({"indexingStatus": "QUEUED"})
    world.dedup.return_value = DedupDecision(virtual_record_id="twin", skip_indexing=True)
    world.count(900)
    await world.run()
    assert world.refused
    world.dedup.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_interrupted_attempt_of_an_over_limit_file_is_refused_not_resumed() -> None:
    world = World({"indexingStatus": "IN_PROGRESS"}, extra_payload={"deliveryId": "d-1"})
    world.resume.return_value = True
    world.count(900)
    await world.run()
    assert world.refused
    world.resume.assert_not_called()


# --- the pure rules ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (MD5, MD5),
        (MD5.upper(), MD5),
        (_gcs(MD5), MD5),
        (bytes.fromhex(MD5), MD5),
        (MD5.encode(), MD5),
        ("W/\"abc\"", None),
        ("", None),
        (None, None),
        (12, None),
        ("g" * 32, None),
        ("A" * 24, None),
    ],
)
def test_md5_normalisation(value: object, expected: str | None) -> None:
    assert page_limit.normalise_md5(value) == expected


def test_the_edition_wording_exists_exactly_once_in_the_module_that_owns_it() -> None:
    import app.utils.user_errors as errors

    assert errors._EDITION_TEXT in pdf_over_page_limit(501)
    assert errors._EDITION_TEXT in PDF_PAGE_LIMIT_NOTE


def test_a_revision_tombstone_is_not_an_unchanged_revision_and_an_md5_still_proves_a_change() -> None:
    tombstone = {"contentRev": f"{page_limit.REFUSED_REV_PREFIX}{REV}", "md5Checksum": MD5}
    assert page_limit.content_state(tombstone, REV, None) == "uncertain"
    assert page_limit.content_state(tombstone, REV, OTHER) == "changed"
    assert page_limit.content_state(tombstone, REV, MD5) == "unchanged"


@pytest.mark.asyncio
async def test_the_early_count_does_not_write_a_page_count_for_a_file_that_is_parsed() -> None:
    """One meaning for ``pageCount``: the pages of the bytes parsed for the record's revision, written by the parse. A
    revision that changed since would otherwise carry the count of the old bytes."""
    world = World({"indexingStatus": "QUEUED"})
    world.count(MAX_INDEXED_PDF_PAGES)
    world.inspected(None)  # the inspection reports no count: nothing is written for this revision
    await world.run()
    assert world.indexed
    assert "pageCount" not in world.graph.record


def test_the_event_handler_applies_the_page_limit_in_one_method() -> None:
    """``check_page_limit`` is called from ``EventProcessor._gate_page_limit`` only, so the gate is one method."""
    import ast
    from pathlib import Path

    import app.events.events as events_module

    tree = ast.parse(Path(events_module.__file__).read_text(encoding="utf-8"))
    callers = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "check_page_limit"
    }
    assert callers == {"_gate_page_limit", "late_check"}, callers


_KEPT_FIELDS = (
    "indexingStatus", "parsingStatus", "processingStartedAt", "contentRev", "reason", "indexingNote", "pageCount",
    "hasIndexedCopy",
)


async def _kept_state(status: str, *, early: bool) -> tuple[World, dict[str, Any]]:
    world = World(searchable(
        indexingStatus=status, parsingStatus="COMPLETED", reason="old reason" if status == "FAILED" else None,
    ))
    world.count(5000 if early else Unreadable())
    world.inspected(5000)
    await world.run()
    return world, {name: world.graph.record.get(name) for name in _KEPT_FIELDS}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETED", "QUEUED", "NOT_STARTED", "FAILED", "AUTO_INDEX_OFF"])
async def test_a_late_keep_ends_in_the_state_an_early_keep_does(status: str) -> None:
    """The run marks the record IN_PROGRESS before the late check; the keep puts that back, so both sites agree."""
    _, early = await _kept_state(status, early=True)
    world, late = await _kept_state(status, early=False)
    assert not world.indexed and not world.refused
    assert late == early


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETED", "QUEUED", "NOT_STARTED", "FAILED", "AUTO_INDEX_OFF"])
async def test_a_late_keep_leaves_nothing_for_the_stale_record_scan(status: str) -> None:
    """Recovery requeues a record left IN_PROGRESS (status or parse status) or with a start time."""
    _, late = await _kept_state(status, early=False)
    assert "IN_PROGRESS" not in (late["indexingStatus"], late["parsingStatus"])
    assert late["processingStartedAt"] is None


@pytest.mark.asyncio
async def test_a_late_keep_discards_the_embed_marker_and_notifies() -> None:
    world = World(searchable())
    world.count(Unreadable())
    world.inspected(600)
    world.stack.enter_context(patch("app.events.events.marker_of", return_value={"attempt": "a1"}))
    discard = world.stack.enter_context(patch.object(world.ep, "_discard_embed_marker", new_callable=AsyncMock))
    await world.run()
    assert not world.indexed
    discard.assert_awaited_once()
    world.notified.assert_awaited_once()
