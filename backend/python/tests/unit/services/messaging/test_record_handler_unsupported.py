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
    record = _failing_record(handler, await _unroutable_error(None, "image/pjpeg"))

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
