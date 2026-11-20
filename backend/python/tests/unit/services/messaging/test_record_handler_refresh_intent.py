"""The record handler's wiring of a Force reindex request (G-46), over ``process_event`` and ``on_message_abandoned``.

The flow tests drive the helpers (``_settle_refresh_intent``, ``_end_refresh_run``) directly; these drive the handler, so a call
that ``process_event`` stopped making is seen: a refresh run that ends COMPLETED, final FAILED or FILE_TYPE_NOT_SUPPORTED clears
the id it saw; a transient failure keeps it; a hand-back raised before the refresh branch keeps it, and the backstop wrote it first;
a purge is keyed by the request's id; an abandoned message clears only the id it carried; a second event of a refresh that ran never
reaches the pipeline and clears a stored copy of its id.
"""
from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, OriginTypes, ProgressStatus
from app.core.org_context import OrgContext, bind
from app.modules.indexing import refresh_intent as ri
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
    StreamMessage,
)
from app.services.messaging.kafka.handlers import record as record_module
from app.services.paid_cache import access as paid_cache_access
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.support.record_handler import make_record_handler
from tests.unit.attempt_start import attempt_start_via

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator

ORG = "org-1"


class _Cache:
    def __init__(self) -> None:
        self.done: set[tuple[str, str, str]] = set()
        self.purges: list[str] = []

    async def refresh_done(self, org: str, record: str, intent_id: str) -> bool:
        return (org, record, intent_id) in self.done

    async def mark_refresh_done(self, org: str, record: str, intent_id: str) -> None:
        self.done.add((org, record, intent_id))

    @asynccontextmanager
    async def refresh_purge(self, org: str, record: str, event_id: str) -> AsyncIterator[bool]:
        self.purges.append(event_id)
        yield False

    def __getattr__(self, name: str):  # noqa: ANN204
        return AsyncMock(return_value=None)


def _world(
    *, status: str = ProgressStatus.QUEUED.value, intent: str | None = "A", mime: str = "application/pdf",
) -> tuple[Any, dict]:
    handler = make_record_handler()
    gp = handler.event_processor.graph_provider
    now = get_epoch_timestamp_in_ms()
    record = {
        "_key": "r1", "orgId": ORG, "virtualRecordId": "vr1", "origin": OriginTypes.UPLOAD.value,
        "indexingStatus": status, "mimeType": mime, "version": 1,
    }
    if intent:
        record.update(ri.intent_fields(intent, now - 1000))
    gp.get_document = AsyncMock(side_effect=lambda *_a, **_k: dict(record))

    async def update_node(_k, _c, fields, *_a, **_kw) -> bool:
        record.update(fields)
        return True

    async def cas(_k, _c, updates, expected, *_a, unset=(), **_kw) -> bool:
        for name, value in expected.items():
            held = record.get(name)
            if (value is None and held is not None) or (value is not None and held != value):
                return False
        record.update(updates)
        for name in unset:
            record.pop(name, None)
        return True

    gp.update_node = AsyncMock(side_effect=update_node)
    gp.update_node_fields_if_match = AsyncMock(side_effect=cas)
    gp.write_attempt_start = attempt_start_via(gp)
    gp.update_queued_duplicates_status = AsyncMock(return_value=0)
    gp.compare_and_set_indexing_status = AsyncMock(side_effect=lambda ids, exp, new, *_a: (
        record.update(indexingStatus=new) or ids) if record["indexingStatus"] == exp else [])
    return handler, record


def _on_event_that(
    record: dict, *, ends: str | None = ProgressStatus.COMPLETED.value, error: Exception | None = None,
) -> Callable[..., AsyncGenerator]:
    async def on_event(*_a, **_k) -> AsyncGenerator:
        record["indexingStatus"] = ProgressStatus.IN_PROGRESS.value
        if ends:
            record["indexingStatus"] = ends
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
        if error is not None:
            raise error
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))
    return on_event


_PAYLOAD = {"recordId": "r1", "orgId": ORG, "virtualRecordId": "vr1", "mimeType": "application/pdf", "extension": "pdf",
            "signedUrl": "https://example.invalid/file.pdf", "version": 1}


def _refresh(intent: str = "A", **extra: object) -> dict:
    return {**_PAYLOAD, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: intent, **extra}


async def _drive(handler, payload: dict, event_type: str = EventTypes.REINDEX_RECORD.value) -> list:
    return [event async for event in handler.process_event(event_type, payload)]


@pytest.fixture
def env() -> Iterator[_Cache]:
    cache = _Cache()
    with ExitStack() as stack:
        stack.enter_context(patch.dict("os.environ", {"REFRESH_INTENT_WRITE": "true"}))
        stack.enter_context(patch.object(paid_cache_access, "paid_cache_for", lambda *_a, **_k: cache))
        stack.enter_context(bind(OrgContext(ORG, None, "job", "job:test", "t")))
        yield cache


async def test_a_refresh_run_that_completes_clears_the_id_it_saw(env) -> None:
    handler, record = _world()
    handler.event_processor.on_event = _on_event_that(record)
    with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"pdf"):
        await _drive(handler, _refresh(is_final_failure=False))
    assert ri.ID_FIELD not in record, record
    assert (ORG, "r1", "A") in env.done


async def test_a_plain_event_on_a_row_with_a_request_runs_as_that_refresh_and_clears_it(env) -> None:
    handler, record = _world()
    seen = {}

    async def on_event(event_data, *_a, **_k):  # noqa: ANN202
        seen.update(event_data["payload"])
        record["indexingStatus"] = ProgressStatus.COMPLETED.value
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

    handler.event_processor.on_event = on_event
    with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"pdf"):
        await _drive(handler, {**_PAYLOAD, "is_final_failure": False}, EventTypes.UPDATE_RECORD.value)
    assert seen.get("forceReindex") is True and seen.get("refreshPaidOutputs") is True and seen.get(ri.PAYLOAD_ID_KEY) == "A"
    assert env.purges == ["A"], env.purges
    assert ri.ID_FIELD not in record


async def test_a_final_failure_clears_the_id_it_saw(env) -> None:
    handler, record = _world()
    handler.event_processor.on_event = _on_event_that(record, ends=None, error=ConnectionError("store down"))
    with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"pdf"):
        with pytest.raises(ConnectionError):
            await _drive(handler, _refresh(is_final_failure=True))
    assert record["indexingStatus"] == ProgressStatus.FAILED.value
    assert ri.ID_FIELD not in record, record


async def test_a_transient_failure_keeps_the_request(env) -> None:
    handler, record = _world()
    handler.event_processor.on_event = _on_event_that(record, ends=None, error=ConnectionError("store down"))
    with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"pdf"):
        with pytest.raises(ConnectionError):
            await _drive(handler, _refresh(is_final_failure=False))
    assert record.get(ri.ID_FIELD) == "A", record
    assert (ORG, "r1", "A") not in env.done


async def test_a_final_failure_does_not_clear_a_newer_request_made_mid_run(env) -> None:
    handler, record = _world()

    async def on_event(*_a, **_k) -> AsyncGenerator:
        record["indexingStatus"] = ProgressStatus.IN_PROGRESS.value
        record.update(ri.intent_fields("B", get_epoch_timestamp_in_ms()))   # the user forces again mid-run
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
        raise ConnectionError("store down")

    handler.event_processor.on_event = on_event
    with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"pdf"):
        with pytest.raises(ConnectionError):
            await _drive(handler, _refresh(is_final_failure=True))
    assert record.get(ri.ID_FIELD) == "B", record
    # the hand-over sends B's refresh now (the producer is the handler's)
    sent = [c.kwargs.get("payload") for c in handler.producer.send_event.await_args_list]
    assert any(p and p.get(ri.PAYLOAD_ID_KEY) == "B" and p.get(ri.FROM_INTENT_KEY) for p in sent), sent


async def test_an_unsupported_file_ends_the_refresh_and_clears_it(env) -> None:
    handler, record = _world(mime="application/x-unknown-qa")
    with patch.object(handler, "_disabled_file_types", new_callable=AsyncMock, return_value=set()):
        await _drive(handler, _refresh(mimeType="application/x-unknown-qa", extension="qaunknown", is_final_failure=False))
    assert record["indexingStatus"] == ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
    assert ri.ID_FIELD not in record, record


async def test_the_backstop_writes_before_a_rate_limit_hand_back(env) -> None:
    handler, record = _world(intent=None)
    # an unstamped refresh (an older producer), and the record is in a provider's wait: the delivery is handed back
    with patch.object(handler, "_handback_remaining_s", return_value=30.0):
        with pytest.raises(Exception) as raised:
            await _drive(handler, {**_PAYLOAD, "forceReindex": True, "refreshPaidOutputs": True, "_delivery_id": "d1",
                                   "is_final_failure": False})
    assert "rate limit" in str(raised.value)
    assert record.get(ri.ID_FIELD) == "d1", record


async def test_a_second_event_of_a_refresh_that_ran_does_nothing(env) -> None:
    handler, record = _world(intent=None, status=ProgressStatus.COMPLETED.value)
    env.done.add((ORG, "r1", "A"))
    handler.event_processor.on_event = AsyncMock(side_effect=AssertionError("must not run"))
    events = await _drive(handler, _refresh(is_final_failure=False))
    assert [e.event for e in events] == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
    assert record["indexingStatus"] == ProgressStatus.COMPLETED.value


async def test_an_abandoned_message_clears_only_the_request_it_carried(env) -> None:
    handler, record = _world(status=ProgressStatus.QUEUED.value)
    msg = StreamMessage(eventType=EventTypes.REINDEX_RECORD.value, payload=_refresh())
    await handler.on_message_abandoned(msg, reason="attempts exhausted", attempts=5)
    assert record["indexingStatus"] == ProgressStatus.FAILED.value
    assert ri.ID_FIELD not in record, record

    handler, record = _world(status=ProgressStatus.QUEUED.value, intent="B")
    await handler.on_message_abandoned(msg, reason="attempts exhausted", attempts=5)
    assert record.get(ri.ID_FIELD) == "B", record


async def test_a_parse_now_event_never_takes_the_request(env) -> None:
    handler, record = _world()
    run = await handler._settle_refresh_intent(EventTypes.NEW_RECORD.value, dict(_PAYLOAD), dict(record), "r1", parse_now=True)
    assert run is None
    assert record.get(ri.ID_FIELD) == "A"


async def test_a_second_event_of_a_refresh_that_ran_also_clears_a_stored_copy_of_its_id(env) -> None:
    """The marker is set but the clear did not land: the second event ends it, so no sweep re-sends it and it never ages out."""
    handler, record = _world(status=ProgressStatus.COMPLETED.value, intent="A")
    env.done.add((ORG, "r1", "A"))
    handler.event_processor.on_event = AsyncMock(side_effect=AssertionError("must not run"))
    await _drive(handler, _refresh(is_final_failure=False))
    assert ri.ID_FIELD not in record and ri.AT_FIELD not in record, record


async def test_a_run_that_outlasts_the_life_still_hands_over_the_request_made_while_it_ran(env) -> None:
    handler, record = _world(intent=None)
    real_now = record_module.get_epoch_timestamp_in_ms
    offset = {"ms": 0}

    def clock() -> int:
        return real_now() + offset["ms"]

    async def on_event(*_a, **_k) -> AsyncGenerator:
        record["indexingStatus"] = ProgressStatus.IN_PROGRESS.value
        record.update(ri.intent_fields("B", clock() + 1))                 # made mid-run ...
        offset["ms"] = ri.INTENT_LIFE_MS + 5_000                           # ... and the run lasts longer than its life
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
        record["indexingStatus"] = ProgressStatus.COMPLETED.value
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

    handler.event_processor.on_event = on_event
    with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"pdf"), patch.object(
        record_module, "get_epoch_timestamp_in_ms", clock,
    ):
        await _drive(handler, {**_PAYLOAD, "is_final_failure": False}, EventTypes.UPDATE_RECORD.value)
    sent = [c.kwargs.get("payload") for c in handler.producer.send_event.await_args_list]
    assert any(p and p.get(ri.PAYLOAD_ID_KEY) == "B" for p in sent), sent


def test_the_record_module_still_imports_unpatched() -> None:
    assert record_module.RefreshRun is not None
