"""Unit tests for the sustained-load test's shape, numbers, checks and comparison.

The load test itself needs a live stack. These pin the parts whose mistakes
would pass a broken run or fail a good one: users joining at the wrong point of
a ramp, the steady window taking in ramp traffic, a non-streaming turn with no
answer counted as a success, a check that misses a failure, and a comparison
that fails a run on a number it only reports.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import bench_load  # noqa: E402
import compare  # noqa: E402
from bench_load import (  # noqa: E402
    LOAD_MIX,
    LoadRecorder,
    Stage,
    Timed,
    active_users,
    build_result,
    evaluate_gate,
    parse_args,
    parse_stages,
    parse_think_time,
    render_summary,
    run_chat_sync,
    run_load,
    stage_index,
    steady_stages,
    target_users,
)
from bench_query import Sample  # noqa: E402

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# The load shape
# --------------------------------------------------------------------------


def test_stages_parse_and_bad_ones_say_what_is_wrong() -> None:
    assert parse_stages("120:4, 60:8,900:8") == (Stage(120, 4), Stage(60, 8), Stage(900, 8))
    for bad, why in (("120", "seconds:users"), ("0:4", "longer than 0"), ("10:0", "a user"), ("", "seconds:users")):
        with pytest.raises(ValueError, match=why):
            parse_stages(bad)


def test_think_time_is_fixed_or_a_range() -> None:
    assert parse_think_time("2") == (2.0, 2.0)
    assert parse_think_time("1:3") == (1.0, 3.0)
    for bad in ("3:1", "-1", "a:b", "1:2:3"):
        with pytest.raises(ValueError):
            parse_think_time(bad)


def test_users_move_linearly_within_a_stage_like_k6() -> None:
    stages = parse_stages("100:4,100:8,100:8,50:0")
    assert target_users(stages, 0) == 0
    assert target_users(stages, 50) == pytest.approx(2)
    assert target_users(stages, 150) == pytest.approx(6)
    assert target_users(stages, 250) == pytest.approx(8)
    assert target_users(stages, 325) == pytest.approx(4)
    assert target_users(stages, 10_000) == 0
    # Rounded down: the fourth user joins once the ramp reaches four, not before.
    assert active_users(stages, 74.9) == 2
    assert active_users(stages, 75) == 3
    assert [stage_index(stages, t) for t in (0, 99.9, 100, 250, 349)] == [0, 0, 1, 2, 3]


def test_the_steady_window_is_the_stages_that_hold_the_peak() -> None:
    assert steady_stages(parse_stages("120:4,120:8,900:8")) == [2]
    assert steady_stages(parse_stages("60:8,300:8,60:8,60:0")) == [1, 2]
    # A pure ramp has no hold: the stage that reaches the peak stands in.
    assert steady_stages(parse_stages("60:4,60:8,60:2")) == [1]


# --------------------------------------------------------------------------
# One non-streaming chat turn
# --------------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int, body: Any = None) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> Any:
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class _Conversations:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self.response = response
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def create_conversation(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.raises:
            raise self.raises
        return self.response


def _answer(content: str = "Revenue grew.", citations: list | None = None) -> dict[str, Any]:
    return {"conversation": {"messages": [
        {"messageType": "user_query", "content": "q"},
        {"messageType": "bot_response", "content": content, "citations": citations or []},
    ]}}


def test_a_non_streaming_turn_with_a_cited_answer_succeeds() -> None:
    client = _Conversations(_Response(201, _answer(citations=[{"citationId": "c1"}])))
    sample = run_chat_sync(client, "What about revenue?", timeout=5)
    assert (sample.operation, sample.ok, sample.with_sources) == ("chat_sync", True, True)
    assert client.calls[0]["json"] == {"query": "What about revenue?", "chatMode": bench_load.CHAT_MODE}


@pytest.mark.parametrize(
    ("client", "error"),
    [
        (_Conversations(_Response(500, {"error": "boom"})), "HTTP 500"),
        (_Conversations(_Response(201)), "response was not JSON"),
        (_Conversations(_Response(201, {"conversation": {"messages": []}})), "response carried no answer"),
        (_Conversations(_Response(201, _answer(content="  "))), "answer was empty"),
        (_Conversations(raises=TimeoutError("read timed out")), "TimeoutError: read timed out"),
    ],
)
def test_a_non_streaming_turn_without_an_answer_is_an_error(client: _Conversations, error: str) -> None:
    sample = run_chat_sync(client, "What about revenue?", timeout=5)
    assert (sample.ok, sample.error) == (False, error)


# --------------------------------------------------------------------------
# The load driver
# --------------------------------------------------------------------------


def _args(**overrides: Any) -> argparse.Namespace:
    args = parse_args(["--label", "unit"])
    args.think_time_range = (0.0, 0.0)
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _fake_operation(fail: set[str] = frozenset(), seconds: float = 0.01):
    calls: list[tuple[str, int]] = []

    def operation(name: str, index: int, turn: int, *_: Any) -> Sample:
        calls.append((name, index))
        time.sleep(seconds)
        ok = name not in fail
        first = seconds / 2 if name == "chat" and ok else None
        return Sample(name, seconds, ok, first, with_sources=ok, error="" if ok else "HTTP 503")

    return operation, calls


def test_the_driver_follows_the_ramp_and_runs_every_operation() -> None:
    # 0 to 2 users over 0.3s, then 2 to 4 over 0.9s: user 0 joins at 0.15s,
    # user 1 at 0.3s, user 2 at 0.75s, and user 3 would at 1.2s, when it ends.
    stages = parse_stages("0.3:2,0.9:4")
    operation, _ = _fake_operation()
    recorder = LoadRecorder()
    wall, aborted = run_load(_args(), stages, "kb-1", {}, recorder, operation)

    assert aborted is None
    assert wall == pytest.approx(1.2, abs=0.8)
    entries = recorder.snapshot()
    assert {e.sample.operation for e in entries} == set(LOAD_MIX)
    assert {e.user for e in entries if e.stage == 0} == {0}
    assert {e.user for e in entries} == {0, 1, 2}
    assert min(e.started for e in entries if e.user == 2) >= 0.75
    assert all(e.user < e.users for e in entries)


def test_a_broken_stack_stops_the_load_early() -> None:
    operation, calls = _fake_operation(fail=set(LOAD_MIX), seconds=0.001)
    recorder = LoadRecorder()
    wall, aborted = run_load(_args(), parse_stages("0.5:4,30:4"), "kb-1", {}, recorder, operation)
    assert aborted is not None and "failed" in aborted
    assert wall < 10


# --------------------------------------------------------------------------
# The result and its checks
# --------------------------------------------------------------------------


@dataclass
class _SeedState:
    status: dict[str, str] = field(default_factory=lambda: {f"r{i}": "COMPLETED" for i in range(10)})
    uploaded_at: dict[str, float] = field(default_factory=lambda: {f"r{i}": 0.0 for i in range(10)})
    stopped_early: str = ""


class _Corpus:
    def describe(self) -> dict[str, Any]:
        return {"docs": 10, "seed": 1337, "kinds": ["txt"]}


def _recorder(stages: tuple[Stage, ...], per_stage: int = 60, fail_every: int = 0,
              slow_ramp: bool = True) -> LoadRecorder:
    """Samples spread over each stage. Ramp stages are made slow on purpose, so a
    steady window that took them in would show it."""
    recorder = LoadRecorder()
    start = 0.0
    held = set(steady_stages(stages))
    n = 0
    for index, stage in enumerate(stages):
        for k in range(per_stage):
            name = LOAD_MIX[k % len(LOAD_MIX)]
            seconds = 9.0 if (slow_ramp and index not in held) else 1.0
            n += 1
            ok = not (fail_every and n % fail_every == 0)
            sample = Sample(name, seconds, ok, 0.5 if name == "chat" else None,
                            with_sources=ok, error="" if ok else "HTTP 503")
            recorder.entries.append(Timed(start + k * stage.seconds / per_stage, stage.users, index, sample))
        start += stage.seconds
    return recorder


def _result(stages: str = "60:4,60:8,300:8", **kwargs: Any) -> dict[str, Any]:
    parsed = parse_stages(stages)
    args = _args(stages=stages, stage_list=parsed)
    recorder = kwargs.pop("recorder", None) or _recorder(parsed, **kwargs)
    return build_result(args, parsed, _Corpus(), _SeedState(), recorder, sum(s.seconds for s in parsed),
                        None, None, {"llm": "azureOpenAI/m", "embedding": "azureOpenAI/e"},
                        datetime.now(timezone.utc))


def test_the_steady_window_leaves_the_ramp_out() -> None:
    result = _result()
    m = result["metrics"]
    assert m["steady_window"] == {"stages": [3], "from_seconds": 120, "seconds": 300, "users": 8}
    # Only the held stage's 60 samples, all fast: the slow ramp stayed out.
    assert m["operations_total"] == 60
    assert m["operations"]["search"]["latency_seconds"]["p95"] == 1.0
    assert m["whole_run"]["operations_total"] == 180
    assert [row["search_p95"] for row in m["stages"]] == [9.0, 9.0, 1.0]
    assert result["gate"] == {"max_error_rate": 0.02, "min_operations": 50, "passed": True, "violations": []}


def test_an_error_rate_over_the_limit_fails_the_run() -> None:
    result = _result(fail_every=20)  # 5% of every stage
    assert result["gate"]["passed"] is False
    assert any("5.0% of all operations failed" in v for v in result["gate"]["violations"])


def test_a_run_that_found_nothing_or_measured_too_little_fails() -> None:
    parsed = parse_stages("60:4,300:4")
    recorder = _recorder(parsed, per_stage=12)
    for entry in recorder.entries:
        entry.sample.with_sources = False
    violations = _result("60:4,300:4", recorder=recorder)["gate"]["violations"]
    assert any("fewer than the 50" in v for v in violations)
    assert any("No search found anything" in v for v in violations)


def test_filtered_searches_that_find_nothing_fail_even_when_plain_ones_do() -> None:
    """A knowledge-base filter that stopped matching hides behind plain searches."""
    parsed = parse_stages("60:4,300:4")
    recorder = _recorder(parsed, per_stage=120)
    for entry in recorder.entries:
        if entry.sample.operation == "search_filtered":
            entry.sample.with_sources = False
    violations = _result("60:4,300:4", recorder=recorder)["gate"]["violations"]
    assert [v for v in violations if "found anything" in v] == [
        "No search filtered to the seeded knowledge base found anything. An empty result is fast and "
        "counts as a success, so this run measured the not-found path; check that the seeded "
        "documents were indexed."
    ]


def test_a_fall_in_search_hits_fails_the_comparison(monkeypatch, tmp_path) -> None:
    base = _result()
    for operation in ("search", "search_filtered"):
        fewer_hits = copy.deepcopy(base)
        fewer_hits["metrics"]["operations"][operation]["with_sources_rate"] = 0.0
        rows, _ = compare.compare(base, fewer_hits)
        flagged = [r for r in rows if r.regressed]
        assert len(flagged) == 1 and flagged[0].gates is True, operation
        assert _run_compare(monkeypatch, tmp_path, base, fewer_hits) == 1, operation


def test_an_operation_that_always_failed_is_named() -> None:
    parsed = parse_stages("60:4,300:4")
    recorder = _recorder(parsed, per_stage=120)
    for entry in recorder.entries:
        if entry.sample.operation == "chat_sync":
            entry.sample.ok = False
            entry.sample.error = "HTTP 500"
    violations = _result("60:4,300:4", recorder=recorder)["gate"]["violations"]
    assert "Every chat_sync operation in the steady window failed." in violations


def test_an_aborted_run_fails_whatever_else_it_measured() -> None:
    metrics = _result()["metrics"]
    assert evaluate_gate(metrics, 0.02, 50, "90% of the last 30 operations failed") == [
        "The load stopped early: 90% of the last 30 operations failed."
    ]
    failing = _result(fail_every=2)["metrics"]
    violations = evaluate_gate(failing, 0.02, 50, "60% of the last 30 operations failed")
    assert len(violations) == 2 and "50.0% of all operations failed" in violations[1]


def test_the_summary_leads_with_the_verdict_and_shows_every_stage() -> None:
    passed = render_summary(_result())
    assert "**Passed.**" in passed
    assert passed.count("\n| 1 | 0→4 |") == 1 and "| 3 | 8→8 |" in passed
    failed = render_summary(_result(fail_every=20))
    assert "**Failed.**" in failed and "of all operations failed" in failed


def test_the_result_is_plain_json() -> None:
    json.dumps(_result())


# --------------------------------------------------------------------------
# The comparison
# --------------------------------------------------------------------------


def _write(tmp_path: Path, name: str, payload: dict[str, Any]) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run_compare(monkeypatch, tmp_path: Path, baseline: dict[str, Any], current: dict[str, Any]) -> int:
    monkeypatch.setattr(sys, "argv", [
        "compare.py", "--baseline", str(_write(tmp_path, "base.json", baseline)),
        "--current", str(_write(tmp_path, "cur.json", current)), "--fail-on-regression",
    ])
    return compare.main()


def _slower(result: dict[str, Any], operation: str, key: str, factor: float) -> dict[str, Any]:
    changed = copy.deepcopy(result)
    block = changed["metrics"]["operations"][operation][key]
    block["p95"] = round(block["p95"] * factor, 3)
    return changed


def test_the_placeholder_baseline_reports_and_never_fails(monkeypatch, tmp_path) -> None:
    placeholder = json.loads(
        (Path(__file__).resolve().parents[1] / "perf" / "baselines" / "ci-load-neo4j-4cpu.json").read_text()
    )
    assert placeholder["placeholder"] is True and placeholder["benchmark"] == "load"
    assert _run_compare(monkeypatch, tmp_path, placeholder, _slower(_result(), "search", "latency_seconds", 5)) == 0


def test_a_p95_regression_past_the_threshold_fails_and_a_small_one_does_not(monkeypatch, tmp_path) -> None:
    base = _result()
    assert _run_compare(monkeypatch, tmp_path, base, _slower(base, "search", "latency_seconds", 1.2)) == 0
    assert _run_compare(monkeypatch, tmp_path, base, _slower(base, "search", "latency_seconds", 1.4)) == 1
    assert _run_compare(monkeypatch, tmp_path, base, _slower(base, "chat_sync", "latency_seconds", 2.5)) == 1
    assert _run_compare(monkeypatch, tmp_path, base, _slower(base, "chat", "first_answer_seconds", 3)) == 1


def test_a_large_share_of_a_tiny_latency_is_not_a_regression(monkeypatch, tmp_path) -> None:
    """On a 100 ms search, a shared runner's jitter alone is tens of percent."""
    base = _result()
    base["metrics"]["operations"]["search"]["latency_seconds"]["p95"] = 0.1
    tripled = _slower(base, "search", "latency_seconds", 3)
    rows, _ = compare.compare(base, tripled)
    assert not next(r for r in rows if r.name == "Search p95").regressed
    assert _run_compare(monkeypatch, tmp_path, base, tripled) == 0
    assert _run_compare(monkeypatch, tmp_path, base, _slower(base, "search", "latency_seconds", 4)) == 1


def test_reported_only_rows_flag_without_failing(monkeypatch, tmp_path) -> None:
    base = _result()
    fewer_citations = copy.deepcopy(base)
    fewer_citations["metrics"]["operations"]["chat"]["with_sources_rate"] = 0.5
    fewer_citations["metrics"]["operations"]["chat_sync"]["with_sources_rate"] = 0.5
    rows, mismatches = compare.compare(base, fewer_citations)
    assert not mismatches
    flagged = [r for r in rows if r.regressed]
    assert [r.name for r in flagged] == ["Answers that cited a document", "Non-streaming answers that cited a document"]
    assert not any(r.gates for r in flagged)
    assert "worse (reported only)" in compare.render(rows, mismatches, "base.json")
    assert _run_compare(monkeypatch, tmp_path, base, fewer_citations) == 0


def test_a_different_load_shape_is_not_judged(monkeypatch, tmp_path) -> None:
    base = _result()
    other = _slower(_result("60:4,60:16,300:16"), "search", "latency_seconds", 5)
    rows, mismatches = compare.compare(base, other)
    assert any(m.startswith("stages:") for m in mismatches)
    assert _run_compare(monkeypatch, tmp_path, base, other) == 0


def test_an_aborted_run_is_not_judged() -> None:
    base = _result()
    aborted = copy.deepcopy(base)
    aborted["metrics"]["aborted"] = "90% of the last 30 operations failed"
    _, mismatches = compare.compare(base, aborted)
    assert mismatches == ["this run: the load stopped early (90% of the last 30 operations failed)"]


def test_query_comparisons_are_unchanged_by_the_load_rows() -> None:
    """The non-streaming row exists only for the load test."""
    import test_perf_query

    base = test_perf_query._query_result()
    rows, _ = compare.compare(base, copy.deepcopy(base))
    assert "Non-streaming answers that cited a document" not in [r.name for r in rows]
    assert all(r.gates for r in rows)
