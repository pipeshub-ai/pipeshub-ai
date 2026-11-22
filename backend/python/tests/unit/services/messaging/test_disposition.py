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
    async def test_forwards_reason_and_attempts(self, logger) -> None:
        sink = AsyncMock()
        message = _message()

        await notify_abandoned(sink, logger, message, reason="poison", attempts=4)

        sink.on_message_abandoned.assert_awaited_once_with(
            message, reason="poison", attempts=4
        )

    @pytest.mark.asyncio
    async def test_a_failing_sink_never_propagates(self, logger) -> None:
        """The caller is on its way to an ack it cannot skip.

        Losing the status write is bad; wedging the stream behind it is worse.
        """
        sink = AsyncMock()
        sink.on_message_abandoned = AsyncMock(side_effect=Exception("graph down"))

        await notify_abandoned(sink, logger, _message(), reason="poison", attempts=1)

    @pytest.mark.asyncio
    async def test_no_sink_is_a_no_op(self, logger) -> None:
        await notify_abandoned(None, logger, _message(), reason="poison", attempts=1)


class TestDescribeMessage:
    """These strings are the only handle an operator has on what was discarded."""

    def test_names_the_record(self) -> None:
        assert "rec-1" in describe_message(_message())

    def test_marks_a_record_less_event(self) -> None:
        described = describe_message(_message(record_id=None))
        assert "none" in described
        assert "newRecord" in described

    def test_marks_an_unparseable_envelope(self) -> None:
        assert "unparseable" in describe_message(None)


class TestSinkProtocol:
    def test_record_handler_shaped_object_satisfies_the_protocol(self) -> None:
        """Consumers duck-type the sink; this is the shape they require."""

        class Handler:
            async def on_message_abandoned(self, message, *, reason, attempts) -> None:
                return None

        assert isinstance(Handler(), AbandonedMessageSink)


class TestSinkRunsUnderTheMessagesOrg:
    @pytest.mark.asyncio
    async def test_the_sink_sees_the_org_of_the_message(self, logger) -> None:
        """A record is read in the sink; without the org bound the read fails closed and the record stays QUEUED."""
        from app.core.org_context import current_org_id

        seen = []

        class Sink:
            async def on_message_abandoned(self, message, *, reason, attempts) -> None:
                seen.append(current_org_id())

        message = StreamMessage(eventType="newRecord", payload={"recordId": "r", "orgId": "org-9"})
        await notify_abandoned(Sink(), logger, message, reason="poison", attempts=4)
        assert seen == ["org-9"]
