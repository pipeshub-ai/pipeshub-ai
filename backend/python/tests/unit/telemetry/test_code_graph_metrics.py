"""Edge-build metrics: outcomes, durations, edges and the reconciler backlog (O-3)."""
from app import platform_events
from app.plugins.telemetry.backend import METRICS_BACKEND


def test_a_finished_build_reports_what_it_did() -> None:
    METRICS_BACKEND.reset()
    platform_events.emit(
        "code_graph.build.settled", outcome="ready", mode="full", duration_seconds=1.5, edges_by_relation={"CALLS": 5},
        external_references=0, unresolved_references=0, ambiguous_references=2, files_without_facts=1,
    )
    platform_events.emit("code_graph.build.settled", outcome="lease_lost")
    platform_events.emit("code_graph.build.request_deferred")
    platform_events.emit("code_graph.builds.reconciled", due=3, waiting=4, started=2)
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
    platform_events.emit("code_graph.tool.called", tool="impact", ok=True)
    platform_events.emit("code_graph.tool.called", tool="impact", ok=True)
    platform_events.emit("code_graph.tool.called", tool="impact", ok=False)
    out = METRICS_BACKEND.serialize()
    assert 'pipeshub_code_graph_tool_calls_total{outcome="ok",tool="impact"} 2.0' in out
    assert 'pipeshub_code_graph_tool_calls_total{outcome="error",tool="impact"} 1.0' in out
