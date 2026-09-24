"""Integration test: plan section 9, Phase 7 — "a saturated parsing service
must not fail records: the record ends COMPLETED after backpressure-driven
retries, and the breaker stays closed."

Exercises a real ``ParsingClient`` (hence its real ``CircuitBreaker``) wired
into a real ``EventProcessor``, faking only the underlying HTTP transport
(429s with ``Retry-After``, then 200). This is the end-to-end companion to
three unit-level tests, none of which alone prove the full round trip:

- ``tests/unit/services/test_base_client_backpressure.py`` — HTTP-layer
  retry/breaker behaviour in isolation (no ``EventProcessor`` involved).
- ``tests/unit/services/messaging/test_error_classifier.py`` —
  ``ParsingClientError(PARSE_BACKPRESSURE)`` classifies ``TRANSIENT`` in
  isolation (no real client/breaker involved).
- ``tests/unit/events/test_orchestrator_flow.py`` — ``EventProcessor``
  happy path, with ``parsing_client`` itself mocked out (no real breaker).

Two scenarios, matching the two backpressure "budgets" plan section 1.4
introduces (see ``base_client.py``):

1. Backpressure clears *within* ``ParsingClient``'s own retry budget — the
   record reaches ``COMPLETED`` from a single ``on_event()`` call.
2. Backpressure *exhausts* that budget (``ParsingClientError`` propagates
   out of ``on_event()``) — proven retryable via the same classifier a real
   consumer uses, then a second ``on_event()`` call (simulating message
   redelivery) reaches ``COMPLETED``.

The breaker must stay closed throughout both.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.events.events import EventProcessor
from app.services.base_client import CircuitState
from app.services.messaging.config import IndexingEvent
from app.services.messaging.error_classifier import (
    MessageErrorClassifier,
    MessageErrorType,
)
from app.services.parsing.client import ParsingClient, ParsingClientError

# ---------------------------------------------------------------------------
# Fakes / factories
# ---------------------------------------------------------------------------


def _make_response(status: int, body: dict | None = None, headers: dict | None = None) -> httpx.Response:
    content = json.dumps(body or {}).encode()
    return httpx.Response(status, content=content, headers=headers or {})


@contextmanager
def _fake_http_client(client: ParsingClient, request_impl):
    """Same double as ``tests/unit/services/test_base_client_backpressure.py``
    — patches ``BaseServiceClient._make_client`` so ``ParsingClient``'s real
    retry/breaker code runs against a scripted sequence of responses instead
    of a socket."""
    mock_httpx = AsyncMock()
    mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
    mock_httpx.__aexit__ = AsyncMock(return_value=False)
    mock_httpx.request = request_impl
    with patch.object(client, "_make_client", return_value=mock_httpx):
        yield mock_httpx


def _make_parse_success_response() -> httpx.Response:
    return _make_response(200, {
        "success": True,
        "block_container": {
            "blocks": [{"index": 0, "type": "text", "data": "hello", "format": "txt"}],
            "block_groups": [],
        },
        "provider_used": "default",
        "metadata": {},
    })


def _make_graph_provider(doc: dict[str, Any]) -> MagicMock:
    """A minimal graph-provider double whose ``update_node`` mutates *doc* in
    place and whose ``get_document`` always returns *doc*'s latest state —
    the same pattern as
    ``tests/unit/events/test_orchestrator_flow.py::test_statuses_track_active_parse_and_index_phases``,
    so a second ``on_event()`` call sees whatever the first one persisted."""
    graph_provider = MagicMock()
    graph_provider.get_document = AsyncMock(side_effect=lambda *_a, **_k: dict(doc))
    graph_provider.get_departments = AsyncMock(return_value=[])
    graph_provider.find_duplicate_records = AsyncMock(return_value=[])
    graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
    graph_provider.batch_update_nodes = AsyncMock(return_value=True)

    async def _update_node(_record_id: str, _collection: str, fields: dict) -> bool:
        doc.update(fields)
        return True

    graph_provider.update_node = AsyncMock(side_effect=_update_node)
    return graph_provider


def _make_doc() -> dict[str, Any]:
    return {
        "_key": "rec-1",
        "orgId": "org-1",
        "recordName": "test.pdf",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "mimeType": "application/pdf",
        "connectorName": None,
        "origin": "UPLOAD",
        "externalRecordId": "ext-rec-1",
        "connectorId": "connector-1",
        "createdAtTimestamp": 1000000,
        "updatedAtTimestamp": 1000000,
    }


def _make_event_data(record_id: str = "rec-1", org_id: str = "org-1") -> dict[str, Any]:
    return {
        "eventType": "newRecord",
        "payload": {
            "recordId": record_id,
            "orgId": org_id,
            "virtualRecordId": "vr-1",
            "extension": "pdf",
            "mimeType": "application/pdf",
            "recordName": "test.pdf",
            "buffer": b"%PDF-1.4 some fake bytes",
            "version": 1,
            "connectorName": "",
        },
    }


def _make_event_processor(
    parsing_client: ParsingClient, graph_provider: MagicMock, doc: dict[str, Any],
) -> EventProcessor:
    # Real SinkOrchestrator.index() persists indexingStatus=COMPLETED via
    # graph_provider.batch_upsert_nodes (app/modules/transformers/
    # sink_orchestrator.py::_update_indexing_status) — mimicked here so this
    # test can assert the record's terminal status directly rather than only
    # "index() was awaited". Everything upstream of this mock (parsing,
    # circuit breaker, EventProcessor orchestration) is real.
    sink_orchestrator = MagicMock()

    async def _index(_ctx: Any) -> None:
        doc["indexingStatus"] = "COMPLETED"

    sink_orchestrator.index = AsyncMock(side_effect=_index)
    return EventProcessor(
        logger=logging.getLogger("test.integration.indexing_backpressure"),
        processor=MagicMock(),
        graph_provider=graph_provider,
        config_service=MagicMock(),
        parsing_client=parsing_client,
        extraction_client=MagicMock(),
        sink_orchestrator=sink_orchestrator,
    )


def _assert_breaker_untouched(parsing_client: ParsingClient) -> None:
    assert parsing_client.circuit_breaker.is_open is False
    assert parsing_client.circuit_breaker._state == CircuitState.CLOSED
    assert parsing_client.circuit_breaker._consecutive_failures == 0


@pytest.mark.asyncio
@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true", "DEFER_EXTRACTION": "true"})
class TestIndexingBackpressureE2E:
    async def test_recovers_within_backpressure_budget_single_call_reaches_completed(self) -> None:
        """429s that clear before ParsingClient's own backpressure budget is
        exhausted never surface as an exception at all — the record reaches
        COMPLETED from a single on_event() call."""
        parsing_client = ParsingClient(service_url="http://fake-parsing:8092", max_retries=3, retry_delay=0.0)
        doc = _make_doc()
        graph_provider = _make_graph_provider(doc)
        ep = _make_event_processor(parsing_client, graph_provider, doc)

        call_count = 0

        async def _flaky_then_healthy(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                return _make_response(429, headers={"Retry-After": "0.001"})
            return _make_parse_success_response()

        with _fake_http_client(parsing_client, _flaky_then_healthy):
            events = [event async for event in ep.on_event(_make_event_data())]

        assert call_count == 4  # 3 backpressured attempts + 1 success, all inside one parse() call
        event_types = [e.event for e in events]
        assert IndexingEvent.PARSING_COMPLETE in event_types
        assert IndexingEvent.INDEXING_COMPLETE in event_types
        assert doc["parsingStatus"] == "COMPLETED"
        assert doc["indexingStatus"] == "COMPLETED"
        _assert_breaker_untouched(parsing_client)

    async def test_budget_exhausted_classifies_transient_then_retry_reaches_completed(self) -> None:
        """429s that outlast ParsingClient's backpressure budget raise
        ParsingClientError(PARSE_BACKPRESSURE) out of on_event() — proven
        retryable via the same classifier a real consumer/handler would use
        (never FAILED) — and a second on_event() call, simulating message
        redelivery once the parsing service has recovered, still reaches
        COMPLETED. The breaker never opens across either attempt."""
        parsing_client = ParsingClient(
            service_url="http://fake-parsing:8092", max_retries=3, retry_delay=0.0,
        )
        parsing_client.max_backpressure_attempts = 2
        doc = _make_doc()
        graph_provider = _make_graph_provider(doc)
        ep = _make_event_processor(parsing_client, graph_provider, doc)

        async def _always_backpressured(method, url, **kwargs):
            return _make_response(429, headers={"Retry-After": "0.001"})

        with _fake_http_client(parsing_client, _always_backpressured):
            with pytest.raises(ParsingClientError) as exc_info:
                async for _event in ep.on_event(_make_event_data()):
                    pass

        assert exc_info.value.code.value == "PARSE_BACKPRESSURE"
        assert (
            MessageErrorClassifier.classify_by_exception(exc_info.value)
            == MessageErrorType.TRANSIENT
        )
        # IN_PROGRESS was written before the parse call; a real consumer
        # would revert this to QUEUED (tests/unit/services/messaging/
        # test_record_handler.py covers that revert in isolation) — this
        # test's concern is that the exception itself is retryable and the
        # breaker is untouched, not re-deriving that bookkeeping.
        assert doc["indexingStatus"] == "IN_PROGRESS"
        _assert_breaker_untouched(parsing_client)

        async def _now_healthy(method, url, **kwargs):
            return _make_parse_success_response()

        with _fake_http_client(parsing_client, _now_healthy):
            events = [event async for event in ep.on_event(_make_event_data())]

        event_types = [e.event for e in events]
        assert IndexingEvent.INDEXING_COMPLETE in event_types
        assert doc["parsingStatus"] == "COMPLETED"
        assert doc["indexingStatus"] == "COMPLETED"
        _assert_breaker_untouched(parsing_client)
