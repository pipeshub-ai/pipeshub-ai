"""A record the legacy dispatch has no branch for fails on its first attempt, not after the retries (F23)."""
from __future__ import annotations

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.utils.user_errors import unsupported_file_type
from tests.unit.events.test_events import _unroutable_error
from tests.unit.services.messaging.test_record_handler import _make_handler
from tests.unit.services.messaging.test_record_handler_password import (
    _failing_record,
    _run,
)


@pytest.mark.asyncio
async def test_a_record_the_dispatch_cannot_route_fails_on_its_first_attempt() -> None:
    handler = _make_handler()
    record = _failing_record(handler, await _unroutable_error(None, "image/heic"))

    await _run(handler, final=False)  # attempt 1 of 3

    assert record["indexingStatus"] == ProgressStatus.FAILED.value
    assert record["reason"] == unsupported_file_type(None)
    warnings = " ".join(str(c) for c in handler.logger.warning.call_args_list)
    assert "Missing is_final_failure" not in warnings


@pytest.mark.asyncio
async def test_an_ordinary_unknown_error_is_still_retried() -> None:
    handler = _make_handler()
    record = _failing_record(handler, RuntimeError("the store hung up"))

    await _run(handler, final=False)

    assert record["indexingStatus"] == ProgressStatus.QUEUED.value


@pytest.mark.asyncio
async def test_a_local_folder_css_file_is_indexed_end_to_end() -> None:
    """The real handler hands a repository file it accepted by name (the local-folder connector
    stamps mimetypes' text/css) to the real dispatch, which reads it instead of refusing it (F23b)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.config.constants.arangodb import EventTypes
    from app.events.events import DedupDecision, EventProcessor
    from tests.unit.attempt_start import attempt_start_via
    from tests.unit.config.file_types import gen_pinned_decisions as gen

    record = {
        "_key": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "recordType": "CODE_FILE",
        "recordName": "style.css", "connectorId": "conn-1", "indexingStatus": ProgressStatus.QUEUED.value,
    }
    handler = gen._gate_handler(record)
    gp = handler.event_processor.graph_provider
    gp.write_attempt_start = attempt_start_via(gp)
    processor = gen._processor()
    handler.event_processor = EventProcessor(gen._Silent(), processor, gp, MagicMock())
    payload = {
        "recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "connectorId": "conn-1",
        "recordName": "style.css", "extension": "css", "mimeType": "text/css",
        "signedUrl": "https://example.invalid/f", "is_final_failure": False,
    }
    with gen._quiet_env(), \
         patch.object(handler, "_download_from_signed_url", new_callable=AsyncMock, return_value=b"body { color: red }"), \
         patch.object(handler.event_processor, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)):
        async for _ in handler.process_event(EventTypes.NEW_RECORD.value, payload):
            pass

    assert processor.routed == ["process_code_document"]
    assert record["indexingStatus"] != ProgressStatus.FAILED.value
