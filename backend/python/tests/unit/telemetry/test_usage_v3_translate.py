"""Usage telemetry v3: what is translated, what is dropped, and the counter deltas."""

import json

from app.plugins.telemetry.backend import NoopBackend, PrometheusBackend
from app.plugins.telemetry.event_buffer import EventBuffer
from app.plugins.telemetry.v3.delta import DeltaTracker
from app.plugins.telemetry.v3.translate import (
    EVENTS,
    METRICS,
    Point,
    label_value,
    translate_event,
    translate_metrics,
)

PERSONAL = {
    "orgId": "org-6f1d",
    "userId": "user-99aa",
    "email": "ada@example-corp.com",
    "domain": "example-corp.com",
    "fullName": "Ada Lovelace",
}


def _activity(value, **labels):
    base = {
        "service": "indexing_service", "activity": "document_indexed", "connector": "Google Drive",
        "status": "ok", "org": PERSONAL["orgId"], "kb": "kb-1", "domain": PERSONAL["domain"], "mimetype": "application/pdf",
    }
    return ("pipeshub_activity_total", "counter", {**base, **labels}, value)


class TestTranslateMetrics:
    def test_dropped_labels_are_summed_and_kept_labels_renamed(self):
        points = translate_metrics([
            _activity(3),
            _activity(4, org="org-2", domain="other.io", kb="kb-9", mimetype="text/plain", service="x"),
            _activity(5, status="failed"),
        ])
        assert points == [
            Point("pipeshub.usage.activity", "counter",
                  (("activity", "document_indexed"), ("connector_type", "google_drive"), ("status", "failed")), 5.0),
            Point("pipeshub.usage.activity", "counter",
                  (("activity", "document_indexed"), ("connector_type", "google_drive"), ("status", "ok")), 7.0),
        ]

    def test_gauges(self):
        points = translate_metrics([
            ("pipeshub_connector_active", "gauge", {"connector": "SLACK", "domain": "a.io"}, 2),
            ("pipeshub_connector_active", "gauge", {"connector": "SLACK", "domain": "b.io"}, 3),
            ("pipeshub_usage_records", "gauge",
             {"connector_type": "Slack", "indexing_status": "COMPLETED", "org": "o1", "domain": "a.io"}, 10),
            ("pipeshub_usage_records", "gauge",
             {"connector_type": "Slack", "indexing_status": "COMPLETED", "org": "o2", "domain": "b.io"}, 5),
            ("pipeshub_usage_records", "gauge",
             {"connector_type": "", "indexing_status": "In Progress!", "org": "o2", "domain": "b.io"}, 1),
        ])
        assert points == [
            Point("pipeshub.usage.connectors.active", "gauge", (("connector_type", "slack"),), 5.0),
            Point("pipeshub.usage.records", "gauge",
                  (("connector_type", "slack"), ("indexing_status", "completed")), 15.0),
            Point("pipeshub.usage.records", "gauge",
                  (("connector_type", "unknown"), ("indexing_status", "in_progress_")), 1.0),
        ]

    def test_node_rows_share_the_table(self):
        points = translate_metrics([
            ("pipeshub_org_users", "gauge", {"org": "o1", "domain": "a.io"}, 4),
            ("pipeshub_org_users", "gauge", {"org": "o2", "domain": "b.io"}, 6),
            ("pipeshub_org_auth_methods", "gauge", {"method": "google", "org": "o1"}, 1),
            ("pipeshub_orgs_total", "gauge", {"account_type": "business"}, 2),
        ])
        assert Point("pipeshub.usage.users", "gauge", (("user_state", "total"),), 10.0) in points
        assert Point("pipeshub.usage.auth_methods", "gauge", (("auth_method", "google"),), 1.0) in points
        assert Point("pipeshub.usage.orgs", "gauge", (("account_type", "business"),), 2.0) in points

    def test_everything_else_is_not_sent(self):
        assert translate_metrics([
            ("pipeshub_http_requests_total", "counter", {"route": "/x"}, 9),
            ("pipeshub_db_query_total", "counter", {}, 9),
            ("pipeshub_metric_collection_enabled", "gauge", {"service": "q"}, 1),
            _activity(float("nan")),
            _activity(-1),
        ]) == []

    def test_counters_come_first(self):
        points = translate_metrics([
            ("pipeshub_connector_active", "gauge", {"connector": "a", "domain": "d"}, 1),
            _activity(1),
        ])
        assert [p.type for p in points] == ["counter", "gauge"]

    def test_no_rule_keeps_a_personal_label(self):
        banned = {"org", "org_id", "orgId", "domain", "email", "user", "user_id", "userId", "kb", "fullName", "name"}
        for rule in METRICS.values():
            assert not banned & set(rule.labels) and not banned & set(rule.labels.values())
        for rule in EVENTS.values():
            assert not banned & set(rule.attributes)
            assert not banned & {new for new, _ in rule.attributes.values()}

    def test_label_value(self):
        assert label_value("connector_type", "Google Drive/Team") == "google_drive_team"
        assert label_value("indexing_status", "") == "unknown"
        assert label_value("indexing_status", None) == "unknown"
        assert label_value("activity", "Document_Indexed") == "Document_Indexed"


def _event(name, **props):
    return {"event": name, "timestamp": "2026-11-25T08:00:00+00:00", "props": {**PERSONAL, **props}}


class TestTranslateEvents:
    def test_search_performed(self):
        out = translate_event(_event("search_performed", status_code=200, num_queries="3", search_type="hybrid"))
        assert out == {
            "name": "search.performed",
            "ts": "2026-11-25T08:00:00+00:00",
            "attributes": {"search_type": "hybrid", "status_code": 200, "num_queries": 3},
        }
        assert type(out["attributes"]["num_queries"]) is int

    def test_chat_session_started_drops_streaming(self):
        out = translate_event(_event("chat_session_started", mode="quick", search_type=None, streaming=True))
        assert out["name"] == "chat.session.started"
        assert out["attributes"] == {"mode": "quick"}

    def test_agent_run_keeps_booleans(self):
        out = translate_event(_event("agent_run", has_tools=True, streaming=False))
        assert out["name"] == "agent.run.started"
        assert out["attributes"] == {"has_tools": True, "streaming": False}

    def test_agent_run_skips_a_value_of_the_wrong_type(self):
        out = translate_event(_event("agent_run", has_tools=1, streaming="yes"))
        assert out["attributes"] == {}

    def test_connector_added_renames(self):
        out = translate_event(_event("connector_added", connector="Google Drive", scope="team"))
        assert out["name"] == "connector.added"
        assert out["attributes"] == {"connector_type": "google_drive", "connector_scope": "team"}

    def test_node_events(self):
        assert translate_event(_event("login", auth_method="google", first_login=True))["attributes"] == {
            "auth_method": "google", "first_login": True,
        }
        assert translate_event(_event("signup"))== {
            "name": "user.signed_up", "ts": "2026-11-25T08:00:00+00:00", "attributes": {},
        }
        assert translate_event(_event("mcp_connected", client_name="claude", client_version="1.2"))["attributes"] == {
            "mcp_client_name": "claude", "mcp_client_version": "1.2",
        }

    def test_events_without_a_v3_counterpart(self):
        for name in ("retrieve_performed", "answer_generated", "pat_created", "anything"):
            assert translate_event(_event(name, status_code=200)) is None

    def test_nothing_personal_survives(self):
        events = [
            _event("search_performed", status_code=200, num_queries=1, search_type="hybrid"),
            _event("chat_session_started", mode="quick", search_type="hybrid", streaming=True),
            _event("agent_run", has_tools=True, streaming=True),
            _event("connector_added", connector="slack", scope="personal"),
            _event("login", auth_method="password", first_login=False),
            _event("org_created"),
        ]
        text = json.dumps([translate_event(e) for e in events])
        for key, value in PERSONAL.items():
            assert key not in text
            assert value not in text

        points = translate_metrics([
            _activity(1),
            ("pipeshub_connector_active", "gauge", {"connector": "slack", "domain": PERSONAL["domain"]}, 1),
            ("pipeshub_usage_records", "gauge", {
                "connector_type": "slack", "indexing_status": "completed",
                "org": PERSONAL["orgId"], "domain": PERSONAL["domain"],
            }, 1),
        ])
        text = json.dumps(points)
        for value in (*PERSONAL.values(), "kb-1", "application/pdf", '"org"', '"domain"', '"kb"'):
            assert value not in text


class TestDeltaTracker:
    def test_cut_returns_the_difference_and_moves_the_baseline(self):
        tracker = DeltaTracker()
        assert tracker.cut({"a": 5, "b": 0}) == {"a": 5}
        assert tracker.cut({"a": 5, "b": 0}) == {}
        assert tracker.cut({"a": 8, "b": 2}) == {"a": 3, "b": 2}

    def test_a_value_below_the_baseline_counts_from_zero(self):
        tracker = DeltaTracker()
        tracker.cut({"a": 10})
        assert tracker.cut({"a": 4}) == {"a": 4}
        assert tracker.cut({"a": 6}) == {"a": 2}

    def test_a_series_that_vanished_and_came_back_counts_from_zero(self):
        tracker = DeltaTracker()
        tracker.cut({"a": 10})
        assert tracker.cut({}) == {}
        assert tracker.cut({"a": 12}) == {"a": 12}

    def test_rebase_takes_current_as_delivered(self):
        tracker = DeltaTracker()
        tracker.rebase({"a": 7})
        assert tracker.cut({"a": 7}) == {}
        assert tracker.cut({"a": 9}) == {"a": 2}

    def test_a_series_left_out_by_the_limit_is_in_the_next_cut(self):
        tracker = DeltaTracker()
        assert tracker.cut({"a": 1, "b": 2, "c": 3}, limit=2) == {"a": 1, "b": 2}
        assert tracker.cut({"a": 1, "b": 2, "c": 4}, limit=2) == {"c": 4}


class TestBackendSnapshot:
    def test_counters_and_gauges_without_created_or_histograms(self):
        backend = PrometheusBackend()
        backend.counter("t_activity_total", "h", ["a"]).inc("x", value=3)
        backend.gauge("t_active", "h", ["b"]).set("y", value=2)
        backend.histogram("t_seconds", "h", ["c"], [1.0]).observe("z", value=0.5)

        assert sorted(backend.snapshot()) == [
            ("t_active", "gauge", {"b": "y"}, 2.0),
            ("t_activity_total", "counter", {"a": "x"}, 3.0),
        ]

    def test_noop_backend_has_nothing(self):
        assert NoopBackend().snapshot() == []


class TestEventBufferTake:
    def test_take_leaves_the_rest_and_requeue_puts_back_in_front(self):
        buffer = EventBuffer()
        for i in range(5):
            buffer.enqueue({"event": str(i)})

        taken = buffer.take(2)
        assert [e["event"] for e in taken] == ["0", "1"]
        assert buffer.size() == 3

        buffer.requeue(taken)
        assert [e["event"] for e in buffer.drain()] == ["0", "1", "2", "3", "4"]
