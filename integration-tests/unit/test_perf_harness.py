"""Unit tests for the harness overhead benchmark (SLO #2) and its comparison.

The benchmark needs a live stack; these pin the parts whose mistakes would
quietly produce wrong numbers: model time subtracted twice or not at all, a
gate judged on one route only, a breakdown that reads the wrong run's lines,
and a comparison that judges two unlike runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import compare  # noqa: E402
from bench_harness import (  # noqa: E402
    BackgroundJob,
    ModelCall,
    Sample,
    ScriptedModel,
    Turn,
    background_peak,
    breakdown,
    gate,
    model_busy,
    parse_logs,
    route_metrics,
    saturate_gate,
)

pytestmark = pytest.mark.unit


def _call(started: float, ended: float, first: float | None = None) -> ModelCall:
    return ModelCall(tag="t", started=started, ended=ended, first_content=first)


def test_model_time_is_the_union_of_calls_inside_the_window() -> None:
    calls = [_call(1.0, 3.0), _call(2.0, 4.0), _call(9.0, 10.0)]
    assert model_busy(calls, 0.0, 5.0) == pytest.approx(3.0)
    assert model_busy(calls, 0.0, 9.5) == pytest.approx(3.5)


def test_time_to_first_token_stops_the_streaming_call_at_its_first_chunk() -> None:
    """The time from the model's first chunk to the client's first token is
    the stack's, not the model's."""
    calls = [_call(0.0, 0.2), _call(0.3, 0.7, first=0.5)]
    assert model_busy(calls, 0.0, 0.6, to_first_token=True) == pytest.approx(0.4)
    assert model_busy(calls, 0.0, 0.6) == pytest.approx(0.5)


def _sample(route: str, ttft: float, done: float, model_ttft: float, model_done: float) -> Sample:
    return Sample(route=route, tag="t", ok=True, ttft_s=ttft, done_s=done,
                  model_before_ttft_s=model_ttft, model_before_done_s=model_done, model_calls=2)


def test_the_gate_is_an_absolute_budget_at_ttft_and_turn_done() -> None:
    metrics = {
        "legacy": route_metrics([_sample("legacy", 0.6, 0.8, 0.4, 0.5)]),
        "harness": route_metrics([_sample("harness", 0.26, 0.45, 0.0, 0.0)]),
    }
    # Non-model: legacy 200 / 300 ms, harness 260 / 450 ms.
    verdict = gate(metrics, ttft_budget_ms=75, done_budget_ms=100)
    assert verdict["ttft_nonmodel"] == {"added_ms": 60.0, "ratio": 1.3, "pass": True}
    assert verdict["done_nonmodel"] == {"added_ms": 150.0, "ratio": 1.5, "pass": False}
    assert verdict["pass"] is False


def test_every_commit_kind_must_stay_within_its_budget() -> None:
    metrics = {
        "legacy": route_metrics([_sample("legacy", 0.6, 0.8, 0.4, 0.5)]),
        "harness": route_metrics([_sample("harness", 0.62, 0.82, 0.4, 0.5)]),
    }
    durable = {"events": {"journal.commit[model.response]": {"ms_p50": 31.0},
                          "journal.commit[pre_run]": {"ms_p50": 9.0},
                          "run.claimed": {"ms_p50": 400.0}}}
    verdict = gate(metrics, commit_budget_ms=25, durable_breakdown=durable)
    assert verdict["commits"] == {
        "journal.commit[model.response]": {"p50_ms": 31.0, "pass": False},
        "journal.commit[pre_run]": {"p50_ms": 9.0, "pass": True},
    }
    assert verdict["pass"] is False
    assert gate(metrics, durable_breakdown=None)["pass"] is True


def test_failed_turns_are_counted_not_measured() -> None:
    failed = Sample(route="harness", tag="t", error="RUN_ERROR boom")
    metrics = route_metrics([failed, _sample("harness", 0.6, 0.8, 0.4, 0.5)])
    assert (metrics["samples"], metrics["ok"]) == (2, 1)
    assert metrics["errors"] == {"RUN_ERROR boom": 1}
    assert metrics["ttft_nonmodel"]["n"] == 1


LOG = """\
2026-09-27 14:36:50,228 - nodejs_service - INFO - [req:a thr:0] [run-admission-service.js:100] - admission.accepted
metadata: {
  "event": "admission.accepted",
  "runId": "run-1",
  "ms": 25
}
2026-09-27 14:36:50,243 - harness_core.driver - INFO - [req:b thr:MainThread task:run-1] [logs.py:108] - run.claimed orgId=o runId=run-1 attempt=1
2026-09-27 14:36:50,276 - app.harness.journal_node - INFO - [req:b thr:MainThread task:run-1] [logs.py:108] - journal.commit orgId=o runId=run-1 commandId=a1:prerun:1 events=3 ms=10
2026-09-27 14:36:50,300 - app.harness.journal_node - INFO - [req:b thr:MainThread task:run-2] [logs.py:108] - journal.commit orgId=o runId=run-2 commandId=a2:model:1 events=3 ms=99
2026-09-27 14:36:50,656 - app.harness.journal_node - INFO - [req:b thr:MainThread task:run-1] [logs.py:108] - journal.commit orgId=o runId=run-1 commandId=a1:model:2 events=6 ms=20
"""


def test_the_breakdown_reads_only_the_bench_runs_and_their_timing_fields() -> None:
    result = breakdown(parse_logs(LOG), {"run-1"})
    assert result["runs"] == 1
    assert result["events"]["journal.commit[model]"] == {
        "per_run": 1.0, "ms_p50": 20.0, "ms_p95": 20.0, "ms_per_run_p50": 20.0,
    }
    assert result["events"]["admission.accepted"]["ms_p50"] == 25.0
    timeline = {row["event"]: row["at_ms"] for row in result["timeline_ms_from_admission_p50"]}
    assert timeline["journal.commit[prerun]#1"] == 48.0
    assert timeline["run.claimed#1"] == 15.0


def _result(label: str, harness_p50: float, model_latency: float = 200) -> dict[str, Any]:
    route = {"samples": 10, "ok": 10, "errors": {},
             "ttft_nonmodel": {"p50_ms": harness_p50, "p95_ms": 300.0},
             "done_nonmodel": {"p50_ms": harness_p50, "p95_ms": 300.0}}
    legacy = {**route, "ttft_nonmodel": {"p50_ms": 200.0, "p95_ms": 300.0},
              "done_nonmodel": {"p50_ms": 200.0, "p95_ms": 300.0}}
    return {"benchmark": "harness", "profile": "short-turn",
            "environment": {"label": label, "model_latency_ms": model_latency, "chunk_interval_ms": 20,
                            "deployment": {"messageBrokerType": "redis"}},
            "metrics": {"legacy": legacy, "harness": route}}


def test_compare_flags_a_harness_route_that_got_slower() -> None:
    rows, mismatches = compare.compare(_result("ci", 200.0), _result("ci", 300.0))
    assert mismatches == []
    flagged = {row.name for row in rows if row.regressed}
    assert flagged == {"harness TTFT p50", "harness turn done p50"}


def test_compare_refuses_runs_with_another_model_latency() -> None:
    _, mismatches = compare.compare(_result("ci", 200.0), _result("ci", 200.0, model_latency=500))
    assert mismatches and "model latency" in mismatches[0]


def test_a_result_round_trips_through_json() -> None:
    assert json.loads(json.dumps(_result("ci", 1.0)))["benchmark"] == "harness"


# ---- saturate (SLO #20, GA-24 on the real stack) ----

_READ = {"tools": [{"type": "function", "function": {"name": "list_toolsets"}}]}


def _asked(results: int) -> dict[str, Any]:
    return {**_READ, "messages": [{"role": "user", "content": "q"}] + [{"role": "tool", "content": "r"}] * results}


def test_ga24_a_scheduled_run_asks_its_steps_at_its_own_latency_and_a_turn_asks_once() -> None:
    model = ScriptedModel("127.0.0.1", 0, 0.2, 0.0, background_latency_s=1.5)
    try:
        model.background["bench-000000000001"] = 3
        assert [model.plan("bench-000000000001", _asked(n)) for n in range(4)] == [
            (True, 1.5), (True, 1.5), (True, 1.5), (False, 1.5)]
        assert [model.plan("bench-000000000002", _asked(n)) for n in range(2)] == [(True, 0.2), (False, 0.2)]
    finally:
        model.server.server_close()


def _turn(ready: float | None) -> Turn:
    turn = Turn(tag="t", admitted_at=10.0)
    turn.first_call_at = None if ready is None else 10.0 + ready
    return turn


def test_ga24_the_saturate_gate_needs_the_p95_within_target_and_every_scheduled_run_completed() -> None:
    jobs = [BackgroundJob(tag=f"bg-{i}", schedule_id=str(i)) for i in range(3)]
    turns = [_turn(r) for r in (0.4, 0.9, 1.2, 2.0)]
    passed = saturate_gate(turns, jobs, {"bg-0", "bg-1", "bg-2"}, ready_slo_ms=5000)
    assert passed["pass"] and 1200.0 < passed["session_ready"]["p95_ms"] <= 2000.0
    assert not saturate_gate(turns, jobs, {"bg-0", "bg-1"}, ready_slo_ms=5000)["pass"], "a scheduled run starved"
    assert not saturate_gate([*turns, _turn(6.0)], jobs, {"bg-0", "bg-1", "bg-2"}, ready_slo_ms=5000)["pass"]
    unmeasured = saturate_gate([*turns, _turn(None)], jobs, {"bg-0", "bg-1", "bg-2"}, ready_slo_ms=5000)
    assert not unmeasured["pass"] and unmeasured["turns_measured"] == 4, "a turn never reached the model is a failure"


def test_the_background_peak_counts_scheduled_runs_in_flight_together() -> None:
    model = ScriptedModel("127.0.0.1", 0, 0.0, 0.0)
    try:
        model.calls = [ModelCall(tag="a", started=0.0, ended=2.0), ModelCall(tag="b", started=1.0, ended=3.0),
                       ModelCall(tag="c", started=2.5, ended=4.0), ModelCall(tag="turn", started=1.5, ended=1.8)]
        assert background_peak(model, {"a", "b", "c"}) == 2
    finally:
        model.server.server_close()
