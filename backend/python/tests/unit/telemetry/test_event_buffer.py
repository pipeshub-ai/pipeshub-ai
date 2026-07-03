"""Unit tests for app.telemetry.event_buffer."""

import threading

from app.telemetry.event_buffer import MAX_EVENTS, EventBuffer, event_buffer, record_event


class TestEventBuffer:
    def test_enqueue_and_size(self):
        buffer = EventBuffer()
        buffer.enqueue({"event": "a"})
        buffer.enqueue({"event": "b"})

        assert buffer.size() == 2

    def test_drain_returns_events_and_empties_buffer(self):
        buffer = EventBuffer()
        buffer.enqueue({"event": "a"})
        buffer.enqueue({"event": "b"})

        drained = buffer.drain()

        assert [e["event"] for e in drained] == ["a", "b"]
        assert buffer.size() == 0
        assert buffer.drain() == []

    def test_overflow_drops_new_events_at_cap(self):
        buffer = EventBuffer()
        for i in range(MAX_EVENTS + 10):
            buffer.enqueue({"event": f"e{i}"})

        assert buffer.size() == MAX_EVENTS
        drained = buffer.drain()
        # Events past the cap are the ones dropped
        assert drained[-1]["event"] == f"e{MAX_EVENTS - 1}"

    def test_concurrent_enqueue_is_thread_safe(self):
        buffer = EventBuffer()

        def worker():
            for _ in range(500):
                buffer.enqueue({"event": "x"})

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert buffer.size() == 2000


class TestRecordEvent:
    def setup_method(self):
        event_buffer.drain()

    def teardown_method(self):
        event_buffer.drain()

    def test_records_event_with_timestamp_and_props(self):
        record_event("search_performed", {"domain": "acme.io"})

        [event] = event_buffer.drain()
        assert event["event"] == "search_performed"
        assert event["props"] == {"domain": "acme.io"}
        # ISO-8601 UTC timestamp
        assert event["timestamp"].endswith("+00:00")

    def test_props_default_to_empty_dict(self):
        record_event("agent_run")

        [event] = event_buffer.drain()
        assert event["props"] == {}
