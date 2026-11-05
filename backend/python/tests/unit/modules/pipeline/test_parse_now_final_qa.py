"""Final QA probes for parse-now (notes/record-metadata-early/QA_B_FINAL.md)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.modules.indexing import parse_now_marker as marker
from app.modules.pipeline.parse_now import (
    READY,
    ParseNow,
    optional_redis,
    parse_outcome,
)
from app.services.messaging.config import StreamMessage
from tests.unit.services.messaging.test_record_handler_parse_now import (
    Harness,
    _payload,
    _record,
)
from tests.unit.utils.test_fetch_record_resolution import (
    MODULE,
    _cached_record,
    _Docs,
    _fetch,
    _provider,
    _row,
    _stored,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(f"{MODULE}._POLL_STEPS", (0.01,))
    monkeypatch.setattr(f"{MODULE}._POLL_THEN_EVERY", 0.01)
    yield
    patch.stopall()


@pytest.fixture
def stored_content() -> Iterator[object]:
    async def fake_get_record(vrid, mapping, *_a, **_kw) -> None:
        mapping[vrid] = _cached_record("rec-1")

    with patch(f"{MODULE}.get_record", side_effect=fake_get_record) as mock:
        yield mock


def _asker(wait: float) -> tuple[ParseNow, AsyncMock]:
    producer = MagicMock()
    producer.send_message = AsyncMock(return_value=True)
    patch(f"{MODULE}.parse_now_wait_seconds", AsyncMock(return_value=wait)).start()
    config = MagicMock()
    config.get_config = AsyncMock(return_value={})
    return ParseNow(config, logging.getLogger("qa"), producer), producer.send_message


# --- parse_outcome: a missing revision matches a missing revision, and only that ---------------------


@pytest.mark.parametrize(("doc", "expected"), [
    ({"indexingStatus": "AUTO_INDEX_OFF", "parseNowStatus": "EMPTY", "parseNowRev": None, "contentRev": None}, "EMPTY"),
    ({"indexingStatus": "AUTO_INDEX_OFF", "parseNowStatus": "EMPTY", "contentRev": None}, "EMPTY"),
    ({"indexingStatus": "AUTO_INDEX_OFF", "parseNowStatus": "EMPTY", "parseNowRev": None, "contentRev": "r2"}, None),
    ({"indexingStatus": "AUTO_INDEX_OFF", "parseNowStatus": "EMPTY", "parseNowRev": "r1", "contentRev": "r2"}, None),
    ({"indexingStatus": "AUTO_INDEX_OFF", "parseNowStatus": "FAILED", "parseNowRev": "r1", "contentRev": "r2"}, None),
    ({"indexingStatus": "AUTO_INDEX_OFF", "parseNowStatus": "STORED", "parseNowRev": None, "contentRev": None}, READY),
    ({"indexingStatus": "AUTO_INDEX_OFF", "parseNowStatus": "STORED", "parseNowRev": "r1", "contentRev": "r1"}, READY),
    ({"indexingStatus": "PAUSED"}, "PAUSED"),
])
async def test_parse_outcome_revision_matching(doc, expected) -> None:
    assert parse_outcome(doc) == expected


# --- FAILED marker: ends the wait, does not block a republish -----------------------------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


async def test_a_failed_marker_ends_the_wait_and_the_next_read_asks_again() -> None:
    failed = _row("AUTO_INDEX_OFF", parseNowStatus="FAILED", parseNowRev="rev-1", parseNowAt=_now_ms())
    asker, send = _asker(wait=5)
    t0 = time.monotonic()
    first = await _fetch(
        ["rec-1"], graph_provider=_provider(_Docs({"rec-1": [_row("AUTO_INDEX_OFF"), failed]})), parse_now=asker,
    )
    assert time.monotonic() - t0 < 1.0
    assert first["not_indexed_records"]["rec-1"]["status"] == "FAILED"
    assert "preparing" not in first["not_indexed_records"]["rec-1"]
    # A later read of a record that still carries the FAILED marker asks again.
    second, send2 = _asker(wait=0.05)
    await _fetch(["rec-1"], graph_provider=_provider(_Docs({"rec-1": [failed]})), parse_now=second)
    assert send.await_count == 1 and send2.await_count == 1


async def test_a_stale_failed_marker_does_not_end_the_wait_of_a_run_just_asked_for(stored_content) -> None:
    stale = _row("FAILED", parseNowStatus="FAILED", parseNowRev="rev-1")
    docs = _Docs({"rec-1": [stale, stale, stale, _stored(stale)]})
    asker, send = _asker(wait=2)

    result = await _fetch(["rec-1"], graph_provider=_provider(docs), parse_now=asker)

    send.assert_awaited_once()
    assert result["ok"] is True


# --- blob read grace: a hung read of what became ready is cut near the budget ----------------------


async def test_a_hung_blob_read_after_ready_is_cut_near_the_budget(monkeypatch) -> None:
    monkeypatch.setattr(f"{MODULE}._FINISH_GRACE_SECONDS", 0.2)

    async def hang(*_a, **_k) -> None:
        await asyncio.sleep(30)

    docs = _Docs({"rec-1": [_row("QUEUED"), _stored(_row("QUEUED"))]})
    asker, _ = _asker(wait=0.2)
    with patch(f"{MODULE}.get_record", side_effect=hang):
        t0 = time.monotonic()
        result = await _fetch(["rec-1"], graph_provider=_provider(docs), parse_now=asker)
        elapsed = time.monotonic() - t0

    assert elapsed < 1.5
    assert result["ok"] is False and "being prepared" in result["error"]


# --- retries exhausted: marker only, never FAILED on the record ---------------------------------------


@pytest.mark.parametrize("status", ["AUTO_INDEX_OFF", "QUEUED", "NOT_STARTED", "IN_PROGRESS", "FAILED"])
async def test_an_abandoned_parse_now_event_writes_only_the_marker(status) -> None:
    harness = Harness(_record(indexingStatus=status))
    message = StreamMessage(eventType=EventTypes.REINDEX_RECORD.value, payload=_payload())

    await harness.handler.on_message_abandoned(message, reason="retries exhausted", attempts=5)

    writes = harness.writes()
    assert [w["parseNowStatus"] for w in writes] == ["FAILED"]
    assert all(set(w) == {"parseNowStatus", "parseNowRev", "parseNowReason", "parseNowAt"} for w in writes)
    harness.graph.compare_and_set_indexing_status.assert_not_awaited()
    harness.handler._trigger_next_queued_duplicate.assert_not_awaited()
    harness.handler._propagate_primary_failure_to_queued_duplicates.assert_not_awaited()


# --- STORED is never overwritten ------------------------------------------------------------------


@pytest.mark.parametrize("fields", [
    {"indexingStatus": "FAILED", "reason": "embedding failed"},
    {"indexingStatus": "QUEUED"},
    {"indexingStatus": "IN_PROGRESS"},
    {"indexingStatus": "EMPTY"},
])
@pytest.mark.parametrize("store_only", [True, False])
async def test_no_status_write_hides_stored_content(fields, store_only) -> None:
    marker.begin_run(marker.ParseNowRun(store_only=store_only, content_rev="r1", stored=True))
    try:
        written = marker.shape_status_write(fields)
    finally:
        marker.end_run()
    assert marker.PARSE_NOW_STATUS not in written


async def test_no_run_means_no_marker_and_no_leak() -> None:
    assert marker.shape_status_write({"indexingStatus": "FAILED"}) == {"indexingStatus": "FAILED"}
    assert marker.current_run() is None


# --- store-only: no record status field survives shaping --------------------------------------------


@pytest.mark.parametrize("status", [
    "QUEUED", "NOT_STARTED", "IN_PROGRESS", "COMPLETED", "FAILED", "EMPTY", "PAUSED", "SKIPPED", "AUTO_INDEX_OFF",
    "PASSWORD_PROTECTED", "FILE_TYPE_NOT_SUPPORTED", "ENABLE_MULTIMODAL_MODELS", "CONNECTOR_OFF", None,
])
async def test_store_only_never_writes_a_record_status(status) -> None:
    every = {
        "indexingStatus": status, "parsingStatus": status, "extractionStatus": status, "processingStartedAt": 1,
        "queuedAtTimestamp": 1, "isDirty": False, "lastIndexTimestamp": 1, "lastExtractionTimestamp": 1,
        "hasIndexedCopy": True, "extractionDeferred": True, "reason": "x", "retryNotBefore": 1,
        "contentRev": "r1", "virtualRecordId": "vr",
    }
    marker.begin_run(marker.ParseNowRun(store_only=True, content_rev="r1"))
    try:
        written = marker.shape_status_write(every)
    finally:
        marker.end_run()
    assert set(written) <= {"contentRev", "virtualRecordId", "parseNowStatus", "parseNowRev", "parseNowReason", "parseNowAt"}


async def test_a_fast_failure_after_a_stale_one_ends_the_wait_in_about_one_poll(stored_content) -> None:
    stale = _row("FAILED", parseNowStatus="FAILED", parseNowRev="rev-1", parseNowAt=1)
    fresh = _row("FAILED", parseNowStatus="FAILED", parseNowRev="rev-1", parseNowAt=_now_ms())
    docs = _Docs({"rec-1": [stale, fresh]})
    asker, send = _asker(wait=5)
    t0 = time.monotonic()

    result = await _fetch(["rec-1"], graph_provider=_provider(docs), parse_now=asker)

    send.assert_awaited_once()
    assert result["ok"] is False
    assert time.monotonic() - t0 < 2.5


async def test_a_stale_failed_marker_alone_does_not_end_the_wait(stored_content) -> None:
    stale = _row("FAILED", parseNowStatus="FAILED", parseNowRev="rev-1", parseNowAt=1)
    asker, _ = _asker(wait=1.5)
    t0 = time.monotonic()

    await _fetch(["rec-1"], graph_provider=_provider(_Docs({"rec-1": [stale]})), parse_now=asker)

    assert time.monotonic() - t0 >= 1.4


async def test_a_folder_is_not_eligible_so_nothing_is_published() -> None:
    asker, send = _asker(wait=5)
    folder = _row("NOT_STARTED", mimeType="application/vnd.folder")

    result = await _fetch(["rec-1"], graph_provider=_provider(_Docs({"rec-1": [folder]})), parse_now=asker)

    send.assert_not_awaited()
    assert result["not_indexed_records"]["rec-1"]["status"] == "FOLDER"


async def test_a_redis_failure_runs_parse_now_without_dedup(caplog) -> None:
    config_service = MagicMock()
    config_service.get_redis_config = AsyncMock(side_effect=ConnectionError("down"))

    with caplog.at_level(logging.WARNING):
        assert await optional_redis(config_service, logging.getLogger("qa")) is None

    assert "Redis unavailable" in caplog.text and "down" in caplog.text


async def test_the_redis_handle_comes_from_the_services_own_config_service() -> None:
    # The container's redis_provider is bound to the base config_service (no key_value_store), so it raised TypeError in
    # the query service; this path must need nothing but get_redis_config().
    from app.config.configuration_service import ConfigurationService
    from app.containers.query import QueryAppContainer
    from app.services.redis.config import RedisConnectionConfig
    from app.services.redis.connection_provider_factory import (
        reset_redis_provider_registry,
    )

    assert QueryAppContainer.config_service.cls is ConfigurationService
    assert "key_value_store" in QueryAppContainer.config_service.kwargs  # the shape that broke the container's provider

    reset_redis_provider_registry()
    config_service = MagicMock()
    config_service.get_redis_config = AsyncMock(
        return_value=MagicMock(host="localhost", port=6379, password=None, db=0, tls=False)
    )
    try:
        provider = await optional_redis(config_service, logging.getLogger("qa"))
    finally:
        reset_redis_provider_registry()

    assert provider is not None and hasattr(provider, "get_client")
    assert RedisConnectionConfig.from_host_port  # signature used above


async def test_a_permanent_marker_ends_the_wait_and_is_not_asked_again() -> None:
    doc = {
        "indexingStatus": "AUTO_INDEX_OFF", "isDeleted": False, "contentRev": "r1",
        "parseNowStatus": "PASSWORD_PROTECTED", "parseNowRev": "r1",
    }
    assert parse_outcome(doc) == "PASSWORD_PROTECTED"
    assert await ParseNow(MagicMock(), logging.getLogger("qa")).assess(MagicMock(), doc) == (False, "PASSWORD_PROTECTED")


# --- the dedup claim: freed once a wait ended for good, kept while the parse may still run ------------


class _Redis:
    def __init__(self) -> None:
        self.keys: set[str] = set()

    async def set(self, key, _value, nx=False, ex=None) -> bool:
        if nx and key in self.keys:
            return False
        self.keys.add(key)
        return True

    async def delete(self, key) -> None:
        self.keys.discard(key)


def _asker_with_redis(wait: float) -> tuple[ParseNow, AsyncMock, _Redis]:
    redis = _Redis()
    provider = MagicMock(key_namespace="", get_client=MagicMock(return_value=redis))
    asker, send = _asker(wait)
    return ParseNow(asker.config_service, logging.getLogger("qa"), asker._producer, provider), send, redis


async def test_the_claim_is_freed_after_a_failed_outcome_so_a_repeat_read_asks_again() -> None:
    failed = _row("AUTO_INDEX_OFF", parseNowStatus="FAILED", parseNowRev="rev-1", parseNowAt=_now_ms())
    asker, send, redis = _asker_with_redis(wait=5)
    await _fetch(
        ["rec-1"], graph_provider=_provider(_Docs({"rec-1": [_row("AUTO_INDEX_OFF"), failed]})), parse_now=asker,
    )
    assert redis.keys == set()
    t0 = time.monotonic()
    await _fetch(["rec-1"], graph_provider=_provider(_Docs({"rec-1": [failed, _row("AUTO_INDEX_OFF"), failed]})), parse_now=asker)
    assert send.await_count == 2 and time.monotonic() - t0 < 1.0


async def test_the_claim_is_kept_when_the_wait_times_out() -> None:
    asker, send, redis = _asker_with_redis(wait=0.05)
    await _fetch(["rec-1"], graph_provider=_provider(_Docs({"rec-1": [_row("AUTO_INDEX_OFF")]})), parse_now=asker)
    assert len(redis.keys) == 1
    await _fetch(["rec-1"], graph_provider=_provider(_Docs({"rec-1": [_row("AUTO_INDEX_OFF")]})), parse_now=asker)
    assert send.await_count == 1


async def test_parallel_reads_send_one_event_even_when_the_outcome_is_final() -> None:
    failed = _row("AUTO_INDEX_OFF", parseNowStatus="FAILED", parseNowRev="rev-1", parseNowAt=_now_ms())
    asker, send, _ = _asker_with_redis(wait=1)
    plain = _row("AUTO_INDEX_OFF")
    # The claim is taken before any wait, so every reader's request meets it.
    await asyncio.gather(*(
        _fetch(["rec-1"], graph_provider=_provider(_Docs({"rec-1": [plain, plain, failed]})), parse_now=asker)
        for _ in range(5)
    ))
    assert send.await_count == 1
