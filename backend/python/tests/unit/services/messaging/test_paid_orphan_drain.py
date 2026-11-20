"""A record deleted while its run is in flight is drained like any deleted record: no status written, no retry, no more paid
calls (S28). The run is the real handler over the real event processor and Markdown processor."""

from __future__ import annotations

import contextlib
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.core.org_context import OrgContext, bind
from app.events.events import DedupDecision, EventProcessor
from app.events.processor import Processor
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    IndexingError,
    RecordDeletedError,
)
from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import DELETED_TTL_MS, InProcessLocks, deleted_name
from app.services.paid_cache.orphan import RecordGone, RecordHandBack, current_guard
from app.services.paid_cache.run import clear_run
from tests.support.paid_cache import MemoryResultStore
from tests.support.record_handler import make_record_handler

ORG, REC = "org-1", "r1"
KEY = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")


class Run:
    """One delivery: the record is live at the start; ``parse`` is what the parser does while it runs."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, marker_at_start: str | None = None) -> None:
        self.locks, self.store = InProcessLocks(), MemoryResultStore()
        self.cache = PaidResultCache(self.store, self.locks)
        monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: self.cache)
        self.paid = 0
        self.gone = False
        self.marker_at_start = marker_at_start
        self.record = {
            "_key": REC, "virtualRecordId": "vr1", "orgId": ORG, "origin": "UPLOAD", "recordType": "FILE", "recordName": "a.md",
            "indexingStatus": ProgressStatus.NOT_STARTED.value, "mimeType": "text/markdown", "extension": "md", "version": 1,
            "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
            "externalRecordId": "ext-1", "connectorId": "conn-1",
        }

    async def pay(self) -> str:
        self.paid += 1
        return "text"

    async def drive(self, parse: Any) -> tuple[MagicMock, Any]:  # noqa: ANN401
        logger = logging.getLogger("test.orphan.drain")
        gp = AsyncMock()
        gp.update_node = AsyncMock(return_value=True)
        from tests.unit.attempt_start import (
            attempt_start_via,  # a shared helper, not a test module
        )

        gp.write_attempt_start = attempt_start_via(gp)
        gp.get_document = AsyncMock(side_effect=lambda *_a, **_k: None if self.gone else dict(self.record))
        if self.marker_at_start is not None:
            await self.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, self.marker_at_start)
        parser = MagicMock()
        parser.extract_and_replace_images.return_value = ("# Hello", [])

        async def parse_to_blocks(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            return await parse(self, *a, **k)

        parser.parse_to_blocks = parse_to_blocks
        with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
            processor = Processor(
                logger=logger, config_service=MagicMock(), indexing_pipeline=MagicMock(), graph_provider=gp,
                parsers={"md": parser}, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
            )
        event_processor = EventProcessor(logger, processor, gp, MagicMock())
        handler = make_record_handler(logger=logger, event_processor=event_processor)
        handler._propagate_primary_failure_to_queued_duplicates = AsyncMock()
        handler._trigger_next_queued_duplicate = AsyncMock()
        payload = {"recordId": REC, "virtualRecordId": "vr1", "orgId": ORG, "recordName": "a.md", "mimeType": "text/markdown",
                   "extension": "md", "is_final_failure": True, "signedUrl": "https://example.com/a.md"}
        with patch.object(event_processor, "_check_duplicate_by_md5", new_callable=AsyncMock,
                          return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
             patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"# Hello"):
            try:
                with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):          # the verified envelope's org
                    async for _ in handler.process_event(EventTypes.NEW_RECORD.value, payload):
                        pass
                raised: BaseException | None = None
            except BaseException as exc:
                raised = exc
        return gp, (handler, raised)

    @staticmethod
    def failed_writes(gp: MagicMock) -> list:
        return [c for c in gp.update_node.call_args_list if c.args[2].get("indexingStatus") == ProgressStatus.FAILED.value]


@pytest.fixture(autouse=True)
def _clean() -> Any:  # noqa: ANN401
    yield
    clear_run()


async def test_a_record_deleted_mid_run_stops_paying_and_is_drained_with_no_status_written(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(monkeypatch)

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await r.cache.get_or_compute(KEY, r.pay)                                   # one paid call, then the delete
        r.gone = True
        await r.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "2000")
        for i in range(5):
            await r.cache.get_or_compute(CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"later{i}"), r.pay)

    gp, (handler, raised) = await run.drive(parse)
    assert isinstance(raised, RecordGone) and isinstance(raised, RecordDeletedError)
    assert run.paid == 1
    assert run.failed_writes(gp) == []
    handler._propagate_primary_failure_to_queued_duplicates.assert_not_called()
    handler._trigger_next_queued_duplicate.assert_not_called()
    assert MessageErrorClassifier.classify_by_exception(raised) == MessageErrorType.TERMINAL


async def test_a_parser_that_swallows_the_stop_and_then_fails_differently_is_still_drained(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(monkeypatch)

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        r.gone = True
        await r.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "2000")
        with contextlib.suppress(Exception):
            await r.cache.get_or_compute(KEY, r.pay)                                # swallowed
        raise ValueError("then it failed on something else")

    gp, (_, raised) = await run.drive(parse)
    assert isinstance(raised, RecordGone)
    assert run.paid == 0 and run.failed_writes(gp) == []


async def test_a_410_whose_confirming_read_failed_hands_the_delivery_back_without_writing_a_status(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(monkeypatch)

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        guard = current_guard(ORG, REC)
        assert guard is not None
        guard.hand_back = True                                                     # what the parsing client records
        raise IndexingError("the parsing service refused a deleted record and the record could not be read")

    gp, (_, raised) = await run.drive(parse)
    assert isinstance(raised, RecordHandBack) and isinstance(raised, RequeueWithoutAttempt)
    assert run.failed_writes(gp) == []


async def test_a_record_re_created_under_a_live_marker_is_not_stopped_by_a_rewritten_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """S13d at the handler: the run started under the old incarnation's marker; the old delete is redelivered (a newer string);
    the record is live with the same creation time, so the paid call is made and the run goes on to fail only on its own."""
    run = Run(monkeypatch, marker_at_start="100")

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await r.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "200")
        await r.cache.get_or_compute(KEY, r.pay)
        raise ValueError("bad file")

    gp, (_, raised) = await run.drive(parse)
    assert isinstance(raised, DocumentProcessingError) and not isinstance(raised, RecordGone)
    assert run.paid == 1


async def test_a_delete_and_quick_re_push_stops_the_old_run(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(monkeypatch)

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await r.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "2000")
        r.record = {**r.record, "createdAtTimestamp": 2}                           # re-pushed: a new incarnation, live
        await r.cache.get_or_compute(KEY, r.pay)

    gp, (_, raised) = await run.drive(parse)
    assert isinstance(raised, RecordGone) and run.paid == 0 and run.failed_writes(gp) == []


async def test_with_the_check_off_nothing_is_bound_and_the_run_is_what_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_ORPHAN_CHECK", "0")
    run = Run(monkeypatch)

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        assert current_guard() is None
        await r.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "2000")
        # the fence still refuses the store, as before; the call itself is paid
        await r.cache.get_or_compute(KEY, r.pay)
        raise ValueError("bad file")

    _, (_, raised) = await run.drive(parse)
    assert isinstance(raised, DocumentProcessingError) and run.paid == 1


_ = logging


async def test_a_run_that_starts_under_a_marker_reads_the_record_once_before_its_first_paid_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """QA-G32B-4 (M7): the handler itself sets ``confirm_first`` when a marker is there at the start, so a graph delete that
    came before the marker write (the marker never changes for this run) is found at the first paid call, by one read."""
    run = Run(monkeypatch, marker_at_start="100")
    seen: dict[str, Any] = {}

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        guard = current_guard(ORG, REC)
        assert guard is not None
        seen["confirm_first"] = guard.confirm_first
        r.gone = True                                                              # deleted in the graph, the marker is unchanged
        await r.cache.get_or_compute(KEY, r.pay)
        seen["paid"] = r.paid

    gp, (_, raised) = await run.drive(parse)
    assert seen == {"confirm_first": True} and isinstance(raised, RecordGone)
    assert run.paid == 0 and run.failed_writes(gp) == []
    # the record read at the start, one confirming read at the first paid call, and the processor's own re-read
    assert gp.get_document.await_count >= 2


async def test_a_run_that_starts_with_no_marker_does_not_confirm_first(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(monkeypatch)
    seen: dict[str, Any] = {}

    async def parse(r: Run, *_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        guard = current_guard(ORG, REC)
        assert guard is not None
        seen["confirm_first"] = guard.confirm_first
        await r.cache.get_or_compute(KEY, r.pay)
        raise ValueError("bad file")

    await run.drive(parse)
    assert seen == {"confirm_first": False} and run.paid == 1
