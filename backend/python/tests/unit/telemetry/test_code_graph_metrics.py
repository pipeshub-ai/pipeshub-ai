"""Edge-build metrics: outcomes, durations, edges and the reconciler backlog (O-3)."""
from app.modules.code_graph.edge_builder import EdgeBuildResult
from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules import code_graph_metrics


def test_a_finished_build_reports_what_it_did() -> None:
    METRICS_BACKEND.reset()
    code_graph_metrics.record_build(
        "ready", EdgeBuildResult(edges_by_type={"CALLS": 5}, ambiguous_skipped=2, facts_missing=1,
                                 duration_s=1.5), "full")
    code_graph_metrics.record_build("lease_lost")
    code_graph_metrics.record_busy_request()
    code_graph_metrics.record_reconcile_sweep(due=3, waiting=4, started=2)
    out = METRICS_BACKEND.serialize()
    for line in ('pipeshub_code_graph_builds_total{outcome="ready"} 1.0',
                 'pipeshub_code_graph_builds_total{outcome="lease_lost"} 1.0',
                 'pipeshub_code_graph_build_duration_seconds_count{mode="full"} 1.0',
                 'pipeshub_code_graph_edges_written_total{relation="CALLS"} 5.0',
                 'pipeshub_code_graph_references_total{result="ambiguous_skipped"} 2.0',
                 'pipeshub_code_graph_events_total{event="files_without_facts"} 1.0',
                 'pipeshub_code_graph_events_total{event="busy_request"} 1.0',
                 'pipeshub_code_graph_events_total{event="reconciler_start"} 2.0',
                 'pipeshub_code_graph_owed_builds{state="due"} 3.0',
                 'pipeshub_code_graph_owed_builds{state="waiting"} 4.0'):
        assert line in out, line


def test_tool_calls_are_counted_by_tool_and_outcome() -> None:
    METRICS_BACKEND.reset()
    code_graph_metrics.record_tool_call("impact", ok=True)
    code_graph_metrics.record_tool_call("impact", ok=True)
    code_graph_metrics.record_tool_call("impact", ok=False)
    out = METRICS_BACKEND.serialize()
    assert 'pipeshub_code_graph_tool_calls_total{outcome="ok",tool="impact"} 2.0' in out
    assert 'pipeshub_code_graph_tool_calls_total{outcome="error",tool="impact"} 1.0' in out
