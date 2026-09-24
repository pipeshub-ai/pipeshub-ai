"""Tests for app.services.messaging.disposition.

The rule these guard: a consumer never abandons a message silently. Before this
existed, a discarded record event left its record on the status it was created
with — QUEUED — which neither the stale-record scan nor the connector sweep
looks at, and the only log line named a stream id that no longer resolved to
anything once the entry was gone.
"""
import logging
from unittest.mock import AsyncMock

import pytest

from app.services.messaging.config import StreamMessage
from app.services.messaging.disposition import (
    AbandonedMessageSink,
    describe_message,
    notify_abandoned,
)


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test.disposition")


def _message(record_id: str | None = "rec-1") -> StreamMessage:
    payload = {"recordId": record_id} if record_id else {"orgId": "org-1"}
    return StreamMessage(eventType="newRecord", payload=payload)


class TestNotifyAbandoned:
    @pytest.mark.asyncio
    async def test_forwards_reason_and_attempts(self, logger):
        sink = AsyncMock()
        message = _message()

        await notify_abandoned(sink, logger, message, reason="poison", attempts=4)

        sink.on_message_abandoned.assert_awaited_once_with(
            message, reason="poison", attempts=4
        )

    @pytest.mark.asyncio
    async def test_a_failing_sink_never_propagates(self, logger):
        """The caller is on its way to an ack it cannot skip.

        Losing the status write is bad; wedging the stream behind it is worse.
        """
        sink = AsyncMock()
        sink.on_message_abandoned = AsyncMock(side_effect=Exception("graph down"))

        await notify_abandoned(sink, logger, _message(), reason="poison", attempts=1)

    @pytest.mark.asyncio
    async def test_no_sink_is_a_no_op(self, logger):
        await notify_abandoned(None, logger, _message(), reason="poison", attempts=1)


class TestDescribeMessage:
    """These strings are the only handle an operator has on what was discarded."""

    def test_names_the_record(self):
        assert "rec-1" in describe_message(_message())

    def test_marks_a_record_less_event(self):
        described = describe_message(_message(record_id=None))
        assert "none" in described
        assert "newRecord" in described

    def test_marks_an_unparseable_envelope(self):
        assert "unparseable" in describe_message(None)


class TestSinkProtocol:
    def test_record_handler_shaped_object_satisfies_the_protocol(self):
        """Consumers duck-type the sink; this is the shape they require."""

        class Handler:
            async def on_message_abandoned(self, message, *, reason, attempts):
                return None

        assert isinstance(Handler(), AbandonedMessageSink)
