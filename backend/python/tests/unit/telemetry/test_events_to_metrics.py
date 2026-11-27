"""Platform events in, the same metrics and event records out.

The application no longer calls telemetry: it emits platform events and this
plug-in turns each into the observation its call site used to make. These tests
drive representative events through the plug-in's handlers and read the
Prometheus registry and the event buffer, the two things the collector receives.
"""

import importlib
import json
import pkgutil
from pathlib import Path

import pytest

from app import platform_events
from app.platform_events import catalog
from app.plugins.telemetry import listeners
from app.plugins.telemetry import modules as metric_modules
from app.plugins.telemetry.backend import METRICS_BACKEND
from app.plugins.telemetry.event_buffer import event_buffer

INVENTORY = Path(__file__).with_name("metric_inventory.json")


def _value(sample: str) -> float:
    """Value of one exposition line, named with its labels in alphabetical order; 0.0 when absent."""
    for line in METRICS_BACKEND.serialize().splitlines():
        if line.startswith(sample + " "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def _delta(sample: str, emit) -> float:
    before = _value(sample)
    emit()
    return _value(sample) - before


def test_the_plugin_listens_to_every_catalogued_event() -> None:
    """Nothing the application says goes unheard. The two governor events are heard by the ops exporter wiring;
    the broker events and the per-call ones (a model call, a tool call, an indexing stage, a retrieval, an outbound
    request, a pool sample) are announced for other plug-ins: no usage metric is defined on them, so this one
    records nothing from them."""
    unheard = set(catalog.EVENTS) - set(listeners.handlers())
    assert unheard == {
        "resource_governor.started", "resource_governor.stopped", "message.published", "message.consumed",
        "llm.call.completed", "tool.call.completed", "indexing.stage.completed", "retrieval.completed",
        "http.client.request.completed", "db.pool.sampled",
    }
    for name, handler in listeners.handlers().items():
        assert platform_events.is_listening(name, handler), name


def test_the_metric_names_types_labels_and_buckets_are_the_ones_shipped_before_the_move() -> None:
    """``metric_inventory.json`` was taken from the code before application modules stopped defining
    instruments. A dashboard or an alert reads these names: changing one is a deliberate edit of that file."""
    for module in pkgutil.iter_modules(metric_modules.__path__):
        importlib.import_module(f"{metric_modules.__name__}.{module.name}")
    now = {}
    for metric in METRICS_BACKEND._instruments:
        entry = {"type": metric._type, "labels": list(metric._labelnames)}
        if metric._type == "histogram":
            entry["buckets"] = list(metric._upper_bounds)
        now[metric._name] = entry
    assert now == json.loads(INVENTORY.read_text())


def test_no_handler_raises_on_its_events_own_fields() -> None:
    """Every handler accepts a payload carrying each catalogued field: a renamed field would otherwise
    only show up as a swallowed listener error in production."""
    samples = {
        "active_by_level": {"org": 1}, "active_by_type": {"slack": 2}, "edges_by_relation": {"CALLS": 1},
        "entities_by_kind": {"person": 1}, "records_by_connector_status": {("slack", "COMPLETED"): 1}, "complete": True, "mentions_by_grounding": {"exact": 1}, "connectors": ["slack"],
        "ok": True, "open": True, "unsafe": False, "streaming": True, "has_tools": False, "demo_sources": False, "stopped": False,
    }
    before = dict(platform_events.listener_errors())
    for name, event in catalog.EVENTS.items():
        payload = {}
        for field in event.fields:
            if field in samples:
                payload[field] = samples[field]
            elif field.endswith(("_seconds", "_at")) or field == "recall":
                payload[field] = 0.5
            elif field in catalog_numbers():
                payload[field] = 1
            else:
                payload[field] = f"t_{field}"
        platform_events.emit(name, **payload)
    assert platform_events.listener_errors() == before


def catalog_numbers() -> set[str]:
    return {
        "count", "due", "started", "waiting", "pending", "stuck", "lag", "partition", "graph_reads", "id_count", "node_count",
        "edge_count", "leg_size", "query_count", "citation_count", "status_code", "consumer_id", "dropped_cap", "rejected",
        "turns", "tool_calls", "tokens_in", "tokens_out", "external_references", "unresolved_references",
        "ambiguous_references", "files_without_facts",
    }


@pytest.mark.parametrize(
    ("event", "payload", "sample", "expected"),
    [
        ("db.query.completed", {"store": "graph", "operation": "t_get", "duration_seconds": 0.01},
         'pipeshub_db_query_seconds_count{op="t_get",store="graph"}', 1),
        ("db.statement.completed", {"service": "t_svc", "site": "t.site", "statement_class": "read", "duration_seconds": 0.01},
         'pipeshub_pg_query_seconds_count{class="read",service="t_svc",site="t.site"}', 1),
        ("message.envelope.rejected", {"topic": "t_topic", "reason": "stale", "event_type": "newRecord"},
         'pipeshub_envelope_rejected_total{event_type="newRecord",reason="stale",topic="t_topic"}', 1),
        ("message.envelope.expired", {"topic": "t_topic2", "event_type": "newRecord"}, 'pipeshub_envelope_stale_total{topic="t_topic2"}', 1),
        ("message.envelope.expired", {"topic": "t_topic3", "event_type": "newRecord"},
         'pipeshub_envelope_stale_by_type_total{event_type="newRecord",topic="t_topic3"}', 1),
        ("document.indexed", {"status": "ok", "connector": "t_conn", "org_id": "t_org", "kb_id": "none", "mime_type": "application/pdf"},
         'pipeshub_activity_total{activity="document_indexed",connector="t_conn",domain="unknown",kb="none",'
         'mimetype="application/pdf",org="t_org",service="indexing_service",status="ok"}', 1),
        ("connector.sync.start_not_recorded", {"connector": "t_conn2"},
         'pipeshub_activity_total{activity="sync_start_not_recorded",connector="t_conn2",domain="unknown",kb="none",'
         'mimetype="none",org="unknown",service="connector_service",status="failed"}', 1),
        ("llm.call.finished", {"call_site": "t_site", "outcome": "ok"}, 'pipeshub_indexing_llm_calls_total{call_site="t_site",outcome="ok"}', 1),
        ("llm.tokens.used", {"call_site": "t_site", "direction": "input", "count": 120.0},
         'pipeshub_indexing_llm_tokens_total{call_site="t_site",direction="input"}', 120),
        ("record.retention.swept", {"kind": "revision", "outcome": "t_kept", "count": 4},
         'pipeshub_record_retention_total{kind="revision",outcome="t_kept"}', 4),
        ("record.retention.swept", {"kind": "revision", "outcome": "t_deleted"},
         'pipeshub_record_retention_total{kind="revision",outcome="t_deleted"}', 1),
        ("trash.purge.finished", {"status": "t_finished", "duration_seconds": 12.0},
         'pipeshub_purge_run_duration_seconds_count{status="t_finished"}', 1),
        ("trash.purge.finished", {"status": "t_paused"}, 'pipeshub_purge_runs_total{status="t_paused"}', 1),
        ("record.soft_deleted", {"source": "t_user", "count": 3}, 'pipeshub_records_soft_deleted_total{source="t_user"}', 3),
        ("vector_search.leg.completed", {"tier": "t_h", "leg_size": 120, "duration_seconds": 0.02},
         'pipeshub_pgvector_leg_seconds_count{leg_band="120",tier="t_h"}', 1),
        ("vector_search.leg.degraded", {"reason": "timeout", "tier": "t_h", "leg_size": 600},
         'pipeshub_pgvector_degraded_total{leg_band="600",reason="timeout",tier="t_h"}', 1),
        ("vector_search.recall.sampled", {"tier": "t_h", "size_band": "s", "share_band": "low", "leg_size": 40, "recall": 0.9},
         'pipeshub_pgvector_recall_sample_count{leg_band="40",share_band="low",size_band="s",tier="t_h"}', 1),
        ("graph_view.request.completed",
         {"operation": "t_expand", "outcome": "ok", "duration_seconds": 0.1, "node_count": 7, "edge_count": 0, "id_count": 0},
         'pipeshub_graph_view_items_total{kind="nodes",operation="t_expand"}', 7),
        ("knowledge_graph.changes.drained", {"stage": "t_drain", "count": 9}, 'pipeshub_kg_drained_rows_total{where="t_drain"}', 9),
        ("permission.shadow.completed", {"result": "t_done", "duration_seconds": 0.01}, 'pipeshub_acl_shadow_seconds_count{result="t_done"}', 1),
        ("paid_result.purge.completed", {"route": "batch", "outcome": "t_purged", "count": 5.0},
         'pipeshub_paid_purge_records_total{outcome="t_purged",route="batch"}', 5),
        ("web.search.completed", {"provider": "t_prov", "outcome": "ok", "duration_seconds": 0.3},
         'pipeshub_web_search_duration_seconds_count{provider="t_prov"}', 1),
        ("code_graph.tool.called", {"tool": "t_tool", "ok": False}, 'pipeshub_code_graph_tool_calls_total{outcome="error",tool="t_tool"}', 1),
        ("indexing.record.dispatched", {"broker": "t_broker", "org_id": "t_org"},
         'pipeshub_indexing_scheduler_dispatched_total{broker="t_broker",org="t_org"}', 1),
        ("org_context.violated", {"kind": "missing", "site": "t_site"}, 'pipeshub_org_context_violations_total{kind="missing",site="t_site"}', 1),
    ],
)
def test_an_event_adds_to_the_series_its_call_site_used_to(event: str, payload: dict, sample: str, expected: float) -> None:
    assert _delta(sample, lambda: platform_events.emit(event, **payload)) == expected


def test_gauges_follow_the_latest_event() -> None:
    platform_events.emit("llm.circuit.changed", provider="t_prov", open=True)
    assert _value('pipeshub_indexing_llm_breaker_open{provider="t_prov"}') == 1.0
    platform_events.emit("llm.circuit.changed", provider="t_prov", open=False)
    assert _value('pipeshub_indexing_llm_breaker_open{provider="t_prov"}') == 0.0
    platform_events.emit("trash.backlog.measured", pending=5, stuck=2, oldest_pending_age_seconds=30.0)
    assert _value('pipeshub_soft_deleted_records{state="pending"}') == 5.0
    assert _value('pipeshub_soft_deleted_records{state="stuck"}') == 2.0
    assert _value('pipeshub_purge_oldest_pending_age_seconds{state="pending"}') == 30.0
    platform_events.emit("indexing.scheduler.measured", broker="t_broker", pending=11, active_by_level={"org": 2, "connector": 3})
    assert _value('pipeshub_indexing_scheduler_buffer_depth{broker="t_broker"}') == 11.0
    assert _value('pipeshub_indexing_scheduler_active_keys{broker="t_broker",level="connector"}') == 3.0


def test_the_connector_inventory_replaces_the_previous_one() -> None:
    platform_events.emit("connector.inventory.counted", active_by_type={"t_slack": 2, ("t_drive", "example.com"): 1})
    assert _value('pipeshub_connector_active{connector="t_slack",domain="unknown"}') == 2.0
    assert _value('pipeshub_connector_active{connector="t_drive",domain="example.com"}') == 1.0
    platform_events.emit("connector.inventory.counted", active_by_type={"t_drive": 4})
    assert _value('pipeshub_connector_active{connector="t_slack",domain="unknown"}') == 0.0
    assert _value('pipeshub_connector_active{connector="t_drive",domain="unknown"}') == 4.0


def test_record_waits_are_the_total_over_the_consumers_that_still_exist() -> None:
    sample = 'pipeshub_indexing_record_waits{broker="t_waits"}'
    platform_events.emit("indexing.record_waits.changed", broker="t_waits", consumer_id=9001, waiting=3)
    platform_events.emit("indexing.record_waits.changed", broker="t_waits", consumer_id=9002, waiting=4)
    assert _value(sample) == 7.0
    platform_events.emit("indexing.record_waits.changed", broker="t_waits", consumer_id=9001, waiting=1)
    assert _value(sample) == 5.0
    platform_events.emit("indexing.consumer.stopped", broker="t_waits", consumer_id=9001)
    assert _value(sample) == 4.0
    # Dropped without a stop: only noted when it is reported (a finalizer may be inside a lock) ...
    platform_events.emit("indexing.consumer.discarded", broker="t_waits", consumer_id=9002)
    assert _value(sample) == 4.0
    # ... and taken out of the total by the next consume pass of any consumer.
    platform_events.emit("indexing.consumer.polled", broker="t_waits")
    assert _value(sample) == 0.0


def test_product_events_reach_the_event_buffer_with_the_property_names_the_collector_knows() -> None:
    event_buffer.drain()
    platform_events.emit(
        "search.performed", org_id="o1", user_id="u1", user_email="a@example.com", email_domain="example.com",
        status_code=200, query_count=2, search_type="search",
    )
    platform_events.emit("retrieval.performed", org_id="o1", user_id="u1", email_domain="example.com", status_code=200, query_count=1)
    platform_events.emit(
        "chat.session.started", org_id="o1", user_id="u1", user_email="a@example.com", email_domain="example.com",
        chat_mode="quick", search_type="internal_search", streaming=True,
    )
    platform_events.emit(
        "agent.run.started", org_id="o1", user_id="u1", user_email="a@example.com", email_domain="example.com",
        has_tools=True, streaming=False,
    )
    platform_events.emit(
        "connector.added", org_id="o1", user_id="u1", user_email="a@example.com", email_domain="example.com",
        connector="slack", scope="team",
    )
    events = {e["event"]: e["props"] for e in event_buffer.drain()}
    assert events == {
        "search_performed": {"orgId": "o1", "userId": "u1", "email": "a@example.com", "domain": "example.com",
                             "status_code": 200, "num_queries": 2, "search_type": "search"},
        "retrieve_performed": {"orgId": "o1", "userId": "u1", "domain": "example.com", "status_code": 200, "num_queries": 1},
        "chat_session_started": {"orgId": "o1", "userId": "u1", "email": "a@example.com", "domain": "example.com",
                                 "mode": "quick", "search_type": "internal_search", "streaming": True},
        "agent_run": {"orgId": "o1", "userId": "u1", "email": "a@example.com", "domain": "example.com",
                      "has_tools": True, "streaming": False},
        "connector_added": {"orgId": "o1", "userId": "u1", "email": "a@example.com", "domain": "example.com",
                            "connector": "slack", "scope": "team"},
    }


def test_an_answer_is_both_an_event_record_and_an_activity_count() -> None:
    event_buffer.drain()
    sample = ('pipeshub_activity_total{activity="answer_generated",connector="demo",domain="example.com",kb="none",'
              'mimetype="none",org="t_answer_org",service="query_service",status="cited"}')
    grew = _delta(sample, lambda: platform_events.emit(
        "answer.generated", org_id="t_answer_org", user_id="u1", email_domain="example.com", chat_mode="quick",
        citation_count=3, connectors=["slack"], demo_sources=True, stopped=False,
    ))
    assert grew == 1
    [record] = event_buffer.drain()
    assert record["event"] == "answer_generated"
    assert record["props"] == {
        "orgId": "t_answer_org", "userId": "u1", "domain": "example.com", "chat_mode": "quick", "citation_count": 3,
        "connectors": ["slack"], "demo_sources": True, "stopped": False,
    }
    assert "email" not in record["props"]  # the address itself stays out of this record
