"""A PDF that waits for Docling goes back in line (QUEUED), even on its last counted delivery; a counted outage fails it."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.services.base_client import ServiceUnavailableError
from app.services.docling.client import DoclingNotServing, DoclingUnavailableError
from tests.support.raised import raised_from
from tests.unit.services.messaging.test_record_handler import _make_handler
from tests.unit.services.messaging.test_record_handler_password import (
    _failing_record,
    _run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


def _record_meeting(handler, error: Exception) -> dict:
    """The record's processor meets *error* before its parse completes, as the PDF path raises it."""
    record = _failing_record(handler, error)

    async def on_event(*_a, **_k) -> AsyncGenerator:
        record["indexingStatus"] = ProgressStatus.IN_PROGRESS.value
        raise error
        yield  # pragma: no cover

    handler.event_processor.on_event = MagicMock(side_effect=on_event)
    return record


def _refused() -> DoclingNotServing:
    return raised_from(
        DoclingNotServing("Docling is not serving"),
        ServiceUnavailableError("DoclingService parse_pdf failed after 3 attempts", delivered=False),
    )


@pytest.mark.parametrize("final", [False, True])
@pytest.mark.asyncio
async def test_a_record_waiting_for_docling_goes_back_in_line(final: bool) -> None:
    handler = _make_handler()
    record = _record_meeting(handler, _refused())

    await _run(handler, final=final)

    assert record["indexingStatus"] == ProgressStatus.QUEUED.value


@pytest.mark.asyncio
async def test_an_outage_docling_saw_still_fails_the_record_on_its_last_attempt() -> None:
    handler = _make_handler()
    record = _record_meeting(handler, DoclingUnavailableError("Docling is not serving"))

    await _run(handler, final=True)

    assert record["indexingStatus"] == ProgressStatus.FAILED.value
