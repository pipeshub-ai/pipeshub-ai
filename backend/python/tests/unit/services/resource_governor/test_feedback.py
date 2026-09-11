"""``DownstreamFeedback``: what services report between two governor samples."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app.services.messaging.backpressure import BackpressureCoordinator
from app.services.resource_governor.feedback import (
    DownstreamFeedback,
    FeedbackWindow,
    get_default_downstream_feedback,
    set_default_downstream_feedback,
)


class TestWindow:
    def test_drain_returns_the_reports_and_resets(self) -> None:
        feedback = DownstreamFeedback()
        feedback.report_throttle("ParsingService")
        feedback.report_timeout("neo4j")
        feedback.report_timeout("neo4j")
        feedback.report_pool_exhausted("redis")
        feedback.report_unavailable("qdrant")

        window = feedback.drain()
        assert window.throttles == {"ParsingService": 1}
        assert window.timeouts == {"neo4j": 2}
        assert window.pool_exhaustions == {"redis": 1}
        assert window.unavailable == {"qdrant": 1}
        assert window.incident
        assert window.timeout_count == 2
        assert not window.is_empty

        assert feedback.drain().is_empty

    def test_timeouts_alone_are_not_an_incident(self) -> None:
        feedback = DownstreamFeedback()
        feedback.report_timeout("neo4j")
        window = feedback.drain()
        assert not window.incident
        assert window.timeout_count == 1

    def test_empty_window(self) -> None:
        window = FeedbackWindow.empty()
        assert window.is_empty
        assert not window.incident
        assert window.timeout_count == 0
        assert window.describe() == "none"

    def test_describe_and_as_dict_are_stable(self) -> None:
        feedback = DownstreamFeedback()
        feedback.report_throttle("b")
        feedback.report_throttle("a")
        feedback.report_timeout("neo4j")
        window = feedback.drain()
        assert window.describe() == "throttled=a:1,b:1 timeouts=neo4j:1"
        assert window.as_dict() == {
            "throttles": {"a": 1, "b": 1},
            "timeouts": {"neo4j": 1},
            "pool_exhaustions": {},
            "unavailable": {},
        }

    def test_reports_from_many_threads_are_all_counted(self) -> None:
        feedback = DownstreamFeedback()
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: feedback.report_timeout("neo4j"), range(2000)))
        assert feedback.drain().timeouts == {"neo4j": 2000}


class TestDefaultInstance:
    def test_default_is_created_once_and_can_be_reset(self) -> None:
        set_default_downstream_feedback(None)
        try:
            first = get_default_downstream_feedback()
            assert get_default_downstream_feedback() is first
            replacement = DownstreamFeedback()
            set_default_downstream_feedback(replacement)
            assert get_default_downstream_feedback() is replacement
        finally:
            set_default_downstream_feedback(None)

    def test_a_coordinator_signal_is_reported_as_a_throttle(self) -> None:
        feedback = DownstreamFeedback()
        set_default_downstream_feedback(feedback)
        try:
            coordinator = BackpressureCoordinator(clock=lambda: 100.0)
            coordinator.signal("ParsingService", 5.0)
            coordinator.signal("ParsingService", 0.0)  # a no-op, not a report
        finally:
            set_default_downstream_feedback(None)
        assert feedback.drain().throttles == {"ParsingService": 1}
