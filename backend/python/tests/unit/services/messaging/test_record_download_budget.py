"""Regression tests for the ``DOWNLOAD_BYTES`` admission budget wired into
``RecordEventHandler`` (adaptive-concurrency plan, phase 2 / section 1.3).

Gating parse/index concurrency alone cannot bound resident memory, because a
file is downloaded into memory *before* any parse slot is requested. These
tests assert bytes are reserved before the body is buffered, held for as
long as the buffer stays resident (through ``on_event`` processing, not just
the HTTP request), and released on every failure path — even when the
download raises after exhausting its retries.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, OriginTypes, ProgressStatus
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.handlers.record import RecordEventHandler
from app.services.resource_governor.gate import AdmissionGate
from app.services.resource_governor.models import Limits, Pool
from app.services.resource_governor.registry import LimitRegistry


class _FakeGovernor:
    """Mimics ``ResourceGovernor.gate()``: one memoised gate per pool, backed
    by a real ``LimitRegistry``/``AdmissionGate`` so budget bookkeeping is
    exercised for real rather than mocked."""

    def __init__(self, download_bytes_limit: int = 64 * 1024 * 1024) -> None:
        self.registry = LimitRegistry(
            Limits(values={p: (download_bytes_limit if p == Pool.DOWNLOAD_BYTES else 4) for p in Pool})
        )
        self._gates: dict[Pool, AdmissionGate] = {}

    def gate(self, pool: Pool) -> AdmissionGate:
        if pool not in self._gates:
            self._gates[pool] = AdmissionGate(pool, self.registry)
        return self._gates[pool]


class _AsyncContextManager:
    def __init__(self, value) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_handler(governor: _FakeGovernor | None) -> RecordEventHandler:
    logger = MagicMock()
    config_service = AsyncMock()
    event_processor = MagicMock()
    event_processor.graph_provider = AsyncMock()
    event_processor.processor = MagicMock()
    event_processor.processor.indexing_pipeline = AsyncMock()
    return RecordEventHandler(
        logger=logger,
        config_service=config_service,
        event_processor=event_processor,
        producer=AsyncMock(),
        governor=governor,
    )


async def _async_gen_events(events):
    for event in events:
        yield event


class TestDownloadReservation:
    @pytest.mark.asyncio
    async def test_reserves_before_buffering_and_holds_on_success(self) -> None:
        governor = _FakeGovernor()
        handler = _make_handler(governor)
        gate = governor.gate(Pool.DOWNLOAD_BYTES)
        file_content = b"x" * (1024 * 1024)  # 1 MiB

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.headers = {"Content-Length": str(len(file_content))}

        async def _iter_chunked(chunk_size):
            yield file_content

        mock_response.content = MagicMock()
        mock_response.content.iter_chunked = _iter_chunked

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        assert gate.in_use == 0
        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                result = await handler._download_from_signed_url(
                    signed_url="https://example.com/file.pdf", record_id="r1", doc={"_key": "r1"},
                )

        assert result == file_content
        # Reservation is held after a successful download — the caller
        # (process_event) releases it once the buffer is actually dropped.
        assert gate.in_use > 0

    @pytest.mark.asyncio
    async def test_releases_when_all_retries_exhausted(self) -> None:
        governor = _FakeGovernor()
        handler = _make_handler(governor)
        gate = governor.gate(Pool.DOWNLOAD_BYTES)

        mock_response = AsyncMock()
        mock_response.status = 500

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
        mock_session_cls = MagicMock(return_value=_AsyncContextManager(mock_session))

        with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientSession", mock_session_cls):
            with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    with pytest.raises(Exception, match="Download failed after 3 attempts"):
                        await handler._download_from_signed_url(
                            signed_url="https://example.com/file.pdf", record_id="r1", doc={"_key": "r1"},
                        )

        assert gate.in_use == 0

    @pytest.mark.asyncio
    async def test_second_download_blocks_until_first_releases(self) -> None:
        """Two concurrent downloads whose combined estimate exceeds the pool
        must serialize rather than both being admitted (the whole point of
        the budget: bound resident bytes across concurrently in-flight
        records, not just within one)."""
        governor = _FakeGovernor(download_bytes_limit=16 * 1024 * 1024)  # exactly the default reservation
        handler = _make_handler(governor)
        gate = governor.gate(Pool.DOWNLOAD_BYTES)

        def _session_factory(file_content: bytes):
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.headers = {}

            async def _iter_chunked(chunk_size):
                yield file_content

            mock_response.content = MagicMock()
            mock_response.content.iter_chunked = _iter_chunked

            mock_session = AsyncMock()
            mock_session.get = MagicMock(return_value=_AsyncContextManager(mock_response))
            return MagicMock(return_value=_AsyncContextManager(mock_session))

        first_done = asyncio.Event()

        async def _download_first() -> None:
            with patch(
                "app.services.messaging.kafka.handlers.record.aiohttp.ClientSession",
                _session_factory(b"a" * 1024),
            ):
                with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                    await handler._download_from_signed_url(
                        signed_url="https://example.com/first.pdf", record_id="r1", doc={"_key": "r1"},
                    )
            first_done.set()

        second_started = False

        async def _download_second() -> None:
            nonlocal second_started
            with patch(
                "app.services.messaging.kafka.handlers.record.aiohttp.ClientSession",
                _session_factory(b"b" * 1024),
            ):
                with patch("app.services.messaging.kafka.handlers.record.aiohttp.ClientTimeout"):
                    await handler._download_from_signed_url(
                        signed_url="https://example.com/second.pdf", record_id="r2", doc={"_key": "r2"},
                    )
            second_started = True

        first_task = asyncio.create_task(_download_first())
        await asyncio.wait_for(first_done.wait(), timeout=1.0)
        # First download holds the whole budget (no Content-Length -> default
        # reservation == the pool limit), so the second must block.
        assert gate.in_use > 0

        second_task = asyncio.create_task(_download_second())
        await asyncio.sleep(0.05)
        assert second_started is False

        gate.release(gate.in_use)  # simulate process_event dropping the first buffer
        await asyncio.wait_for(second_task, timeout=1.0)
        assert second_started is True

        await first_task


class TestProcessEventBudgetLifecycle:
    @pytest.mark.asyncio
    async def test_budget_released_only_after_on_event_completes(self) -> None:
        governor = _FakeGovernor()
        handler = _make_handler(governor)
        gate = governor.gate(Pool.DOWNLOAD_BYTES)

        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "connectorId": "conn-1",
            "origin": OriginTypes.CONNECTOR.value,
            "mimeType": "application/pdf",
        }
        connector_instance = {"_key": "conn-1", "isActive": True}
        gp.get_document = AsyncMock(side_effect=[record, connector_instance, record])
        gp.update_queued_duplicates_status = AsyncMock()

        in_use_during_processing = None

        async def _on_event(_event_data):
            nonlocal in_use_during_processing
            in_use_during_processing = gate.in_use
            yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1"))
            yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1"))

        handler.event_processor.on_event = MagicMock(side_effect=_on_event)

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            async def _fake_download(*, signed_url, record_id, doc, budget=None, from_route=False):
                if budget is not None:
                    await budget.reserve(2 * 1024 * 1024)
                return b"file content"

            mock_dl.side_effect = _fake_download

            events = []
            async for event in handler.process_event(EventTypes.NEW_RECORD.value, payload):
                events.append(event)

        assert len(events) == 2
        # The reservation was still held while on_event was running...
        assert in_use_during_processing is not None
        assert in_use_during_processing > 0
        # ...and fully released once process_event finished.
        assert gate.in_use == 0

    @pytest.mark.asyncio
    async def test_failed_signed_url_download_releases_before_fallback(self) -> None:
        """If the signed-URL download exhausts its retries, its reservation
        must not leak into the connector-streaming fallback attempt."""
        governor = _FakeGovernor()
        handler = _make_handler(governor)
        gate = governor.gate(Pool.DOWNLOAD_BYTES)

        gp = handler.event_processor.graph_provider
        record = {
            "_key": "r1",
            "virtualRecordId": "vr1",
            "indexingStatus": ProgressStatus.NOT_STARTED.value,
            "origin": OriginTypes.UPLOAD.value,
            "mimeType": "application/pdf",
        }
        gp.get_document = AsyncMock(side_effect=[record, record])
        gp.update_queued_duplicates_status = AsyncMock()
        handler.config_service.get_config = AsyncMock(
            return_value={"connectors": {"endpoint": "https://connectors.internal"}}
        )

        handler.event_processor.on_event = MagicMock(
            return_value=_async_gen_events([
                PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="r1")),
                PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="r1")),
            ])
        )

        payload = {
            "recordId": "r1",
            "orgId": "org-1",
            "mimeType": "application/pdf",
            "extension": "pdf",
            "signedUrl": "https://example.com/file.pdf",
        }

        async def _failing_download(*, signed_url, record_id, doc, budget=None, from_route=False):
            if budget is not None:
                await budget.reserve(5 * 1024 * 1024)
                budget.release()  # mirrors the real retry-exhaustion cleanup
            raise Exception("Download failed after 3 attempts")

        with patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock) as mock_dl:
            mock_dl.side_effect = _failing_download
            with patch("app.services.messaging.kafka.handlers.record.generate_jwt", new_callable=AsyncMock):
                with patch(
                    "app.services.messaging.kafka.handlers.record.make_api_call", new_callable=AsyncMock
                ) as mock_api:
                    async def _fake_api_call(*, route, token, byte_budget=None):
                        if byte_budget is not None:
                            await byte_budget.reserve(1024)
                        return {"data": b"fallback content"}

                    mock_api.side_effect = _fake_api_call

                    events = []
                    async for event in handler.process_event(EventTypes.NEW_RECORD.value, payload):
                        events.append(event)

        assert len(events) == 2
        # Signed-URL attempt's reservation was released before the fallback
        # started, and the fallback's own reservation was released once
        # on_event finished — nothing should still be held.
        assert gate.in_use == 0
