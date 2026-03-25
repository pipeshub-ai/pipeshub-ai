"""Unit tests for app.connectors.core.base.event_service.event_service.

Covers:
- BaseEventService.__init__: logger and subscribers initialization
- BaseEventService.publish_event: success, subscriber callback, callback error, no subscribers
- BaseEventService.subscribe_to_events: success, multiple event types, exception
- BaseEventService.unsubscribe_from_events: success, exception
- BaseEventService.process_event: success, exception
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.core.base.event_service.event_service import BaseEventService


# ---------------------------------------------------------------------------
# Concrete subclass for testing (BaseEventService is ABC)
# ---------------------------------------------------------------------------


class ConcreteEventService(BaseEventService):
    """Concrete subclass for testing abstract BaseEventService."""
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event_service(logger=None):
    """Create a ConcreteEventService with a mock logger."""
    if logger is None:
        logger = MagicMock(spec=logging.Logger)
    return ConcreteEventService(logger=logger)


# ===========================================================================
# __init__
# ===========================================================================


class TestInit:
    def test_attributes(self):
        logger = MagicMock(spec=logging.Logger)
        svc = ConcreteEventService(logger=logger)
        assert svc.logger is logger
        assert svc._subscribers == {}


# ===========================================================================
# publish_event
# ===========================================================================


class TestPublishEvent:
    @pytest.mark.asyncio
    async def test_publish_no_subscribers(self):
        """Publishing with no subscribers returns True."""
        svc = _make_event_service()
        result = await svc.publish_event("test_event", {"key": "value"})
        assert result is True

    @pytest.mark.asyncio
    async def test_publish_with_subscriber(self):
        """Subscribers receive the event data."""
        svc = _make_event_service()
        callback = AsyncMock()
        svc._subscribers = {"test_event": [callback]}

        result = await svc.publish_event("test_event", {"key": "value"})

        assert result is True
        callback.assert_awaited_once_with({"key": "value"})

    @pytest.mark.asyncio
    async def test_publish_multiple_subscribers(self):
        """Multiple subscribers for the same event type all receive the event."""
        svc = _make_event_service()
        cb1 = AsyncMock()
        cb2 = AsyncMock()
        svc._subscribers = {"test_event": [cb1, cb2]}

        result = await svc.publish_event("test_event", {"data": 1})

        assert result is True
        cb1.assert_awaited_once_with({"data": 1})
        cb2.assert_awaited_once_with({"data": 1})

    @pytest.mark.asyncio
    async def test_publish_callback_error_handled(self):
        """Error in one callback is logged but does not stop others."""
        svc = _make_event_service()
        cb_fail = AsyncMock(side_effect=RuntimeError("callback error"))
        cb_ok = AsyncMock()
        svc._subscribers = {"test_event": [cb_fail, cb_ok]}

        result = await svc.publish_event("test_event", {"data": 1})

        assert result is True
        cb_fail.assert_awaited_once()
        cb_ok.assert_awaited_once()
        svc.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_publish_event_type_not_subscribed(self):
        """Event type with no subscribers returns True."""
        svc = _make_event_service()
        svc._subscribers = {"other_event": [AsyncMock()]}

        result = await svc.publish_event("unsubscribed_event", {})
        assert result is True

    @pytest.mark.asyncio
    async def test_publish_general_exception_returns_false(self):
        """General exception during publish returns False."""
        svc = _make_event_service()
        # Make _subscribers access raise
        svc._subscribers = MagicMock()
        svc._subscribers.__contains__ = MagicMock(side_effect=RuntimeError("bad"))

        result = await svc.publish_event("event", {})
        assert result is False
        svc.logger.error.assert_called()


# ===========================================================================
# subscribe_to_events
# ===========================================================================


class TestSubscribeToEvents:
    @pytest.mark.asyncio
    async def test_subscribe_single_event_type(self):
        """Subscribing to a single event type registers the callback."""
        svc = _make_event_service()
        callback = AsyncMock()

        sub_id = await svc.subscribe_to_events(["new_record"], callback)

        assert sub_id.startswith("sub_")
        assert callback in svc._subscribers["new_record"]

    @pytest.mark.asyncio
    async def test_subscribe_multiple_event_types(self):
        """Subscribing to multiple event types registers the callback for each."""
        svc = _make_event_service()
        callback = AsyncMock()

        sub_id = await svc.subscribe_to_events(["event_a", "event_b"], callback)

        assert sub_id.startswith("sub_")
        assert callback in svc._subscribers["event_a"]
        assert callback in svc._subscribers["event_b"]

    @pytest.mark.asyncio
    async def test_subscribe_adds_to_existing(self):
        """Subscribing adds to existing subscriber list."""
        svc = _make_event_service()
        existing_cb = AsyncMock()
        new_cb = AsyncMock()
        svc._subscribers = {"event_a": [existing_cb]}

        await svc.subscribe_to_events(["event_a"], new_cb)

        assert len(svc._subscribers["event_a"]) == 2
        assert existing_cb in svc._subscribers["event_a"]
        assert new_cb in svc._subscribers["event_a"]

    @pytest.mark.asyncio
    async def test_subscribe_exception_returns_empty(self):
        """Exception during subscribe returns empty string."""
        svc = _make_event_service()
        # Make datetime.now() raise to trigger exception path
        with patch("app.connectors.core.base.event_service.event_service.datetime") as mock_dt:
            mock_dt.now.side_effect = RuntimeError("time error")
            sub_id = await svc.subscribe_to_events(["event"], AsyncMock())

        assert sub_id == ""
        svc.logger.error.assert_called()


# ===========================================================================
# unsubscribe_from_events
# ===========================================================================


class TestUnsubscribeFromEvents:
    @pytest.mark.asyncio
    async def test_unsubscribe_returns_true(self):
        """Default unsubscribe returns True."""
        svc = _make_event_service()
        result = await svc.unsubscribe_from_events("sub_123")
        assert result is True

    @pytest.mark.asyncio
    async def test_unsubscribe_logs_info(self):
        """Unsubscribe logs the subscription ID."""
        svc = _make_event_service()
        await svc.unsubscribe_from_events("sub_456")
        svc.logger.info.assert_called()


# ===========================================================================
# process_event
# ===========================================================================


class TestProcessEvent:
    @pytest.mark.asyncio
    async def test_process_returns_true(self):
        """Default process_event returns True."""
        svc = _make_event_service()
        result = await svc.process_event("sync_complete", {"records": 100})
        assert result is True

    @pytest.mark.asyncio
    async def test_process_logs_info(self):
        """process_event logs the event type."""
        svc = _make_event_service()
        await svc.process_event("sync_start", {})
        svc.logger.info.assert_called()


# ===========================================================================
# Integration: subscribe + publish + receive
# ===========================================================================


class TestIntegration:
    @pytest.mark.asyncio
    async def test_subscribe_then_publish(self):
        """Full flow: subscribe to event, publish event, verify callback called."""
        svc = _make_event_service()
        received_data = []

        async def on_event(data):
            received_data.append(data)

        await svc.subscribe_to_events(["sync_complete"], on_event)
        await svc.publish_event("sync_complete", {"records": 42})

        assert len(received_data) == 1
        assert received_data[0]["records"] == 42

    @pytest.mark.asyncio
    async def test_publish_only_to_matching_type(self):
        """Only callbacks for the matching event type are invoked."""
        svc = _make_event_service()
        cb_a = AsyncMock()
        cb_b = AsyncMock()

        await svc.subscribe_to_events(["event_a"], cb_a)
        await svc.subscribe_to_events(["event_b"], cb_b)

        await svc.publish_event("event_a", {"x": 1})

        cb_a.assert_awaited_once_with({"x": 1})
        cb_b.assert_not_awaited()
