"""Unit tests for the collaborative-chats poll bench's numbers and its comparison with a baseline."""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "perf"))

import bench_collab_poll as bench  # noqa: E402
import compare  # noqa: E402
import collab_explain  # noqa: E402


@dataclass
class Call:
    user_id: str
    at: float


def result(p95: float = 0.02, rate_limited: int = 0, direct_calls: int = 0) -> dict:
    metrics = {
        "latency_seconds": {"p50": 0.01, "p95": p95, "p99": 0.1},
        "requests_per_second": 80,
        "not_modified_ratio": 0.95,
        "status_counts": {"200": 5, "304": 90, "429": rate_limited},
        "errors": 0,
        "python_team_id_calls": {"total": 10 + direct_calls, "by_population": {"owner_direct": direct_calls, "team_only": 10}},
    }
    return {
        "benchmark": "collab_poll",
        "environment": {"label": "x"},
        "profile": {"viewers": 200, "windows": 450, "duration_seconds": 180, "cadence": {}, "mix": {}},
        "corpus": {"sessions": 1000, "seed": 1},
        "metrics": metrics,
    }


def test_percentile_interpolates_like_numpy() -> None:
    assert bench.percentile([1, 2, 3, 4], 50) == 2.5
    assert bench.percentile([], 95) is None


def test_team_calls_are_split_by_population_and_single_flight_is_measured() -> None:
    calls = [Call("a", 0.0), Call("a", 0.4), Call("b", 5.0)]
    stats = bench.team_call_stats(calls, {"a": "team_only", "b": "owner_direct"})  # type: ignore[arg-type]
    assert stats["by_population"] == {"team_only": 2, "owner_direct": 1}
    assert stats["max_calls_by_one_user_within_1s"] == 2


def test_a_slower_p95_is_a_gating_regression() -> None:
    rows, mismatches = compare.compare(result(0.02), result(0.05))
    assert not mismatches
    assert any(r.name == "Feed poll p95" and r.regressed and r.gates for r in rows)


def test_new_429s_and_direct_team_calls_are_reported_but_never_gate() -> None:
    rows, _ = compare.compare(result(), result(rate_limited=7, direct_calls=3))
    flagged = {r.name: r for r in rows if r.regressed}
    assert {"Polls refused with 429", "Python team-id calls for owner and direct viewers"} <= set(flagged)
    assert not any(r.gates for r in flagged.values())


def test_runs_of_a_different_size_are_not_judged() -> None:
    other = copy.deepcopy(result())
    other["profile"]["viewers"] = 2000
    _, mismatches = compare.compare(result(), other)
    assert mismatches


def test_explain_walks_nested_stages_and_finds_collscan() -> None:
    plan = {"stage": "SORT", "inputStage": {"stage": "FETCH", "inputStage": {"stage": "OR", "inputStages": [{"stage": "COLLSCAN"}, {"stage": "IXSCAN"}]}}}
    assert [s["stage"] for s in collab_explain.walk_stages(plan)] == ["SORT", "FETCH", "OR", "COLLSCAN", "IXSCAN"]


def test_upstream_load_is_per_active_user_per_minute_with_a_bucket_p95() -> None:
    before = {"collab_teamids_upstream_seconds_count": {"ok": 10.0, "error": 0.0}}
    after = {"collab_teamids_upstream_seconds_count": {"ok": 70.0, "error": 2.0}}
    inf = float("inf")
    b0 = {0.05: 10.0, 0.1: 10.0, inf: 10.0}
    b1 = {0.05: 50.0, 0.1: 80.0, inf: 82.0}
    stats = bench.upstream_stats(before, after, b0, b1, active_users=10, elapsed=120.0, team_only_users=4)
    assert stats["lookups"] == 62 and stats["errors"] == 2
    assert stats["per_active_user_per_minute"] == 3.1
    assert stats["per_team_only_viewer_per_minute"] == 7.75
    assert stats["miss_p95_seconds"] == 0.1
    assert bench.bucket_percentile(b0, b0, 95) is None


def test_perf_04_checks_judge_load_and_errors_not_hit_rate() -> None:
    def metrics(per_user: float, p95: float | None, error_ratio: float | None) -> dict:
        return {
            "latency_seconds": {"p95": 0.01},
            "python_team_id_calls": {"by_population": {}, "max_calls_by_one_user_within_1s": 1},
            "rate_limited": 0,
            "errors": 0,
            "teamids_cache": {"error_ratio": error_ratio},
            "teamids_upstream": {"per_active_user_per_minute": 0.1, "per_team_only_viewer_per_minute": per_user, "miss_p95_seconds": p95},
        }

    ok = {c["id"]: c["ok"] for c in bench.checks(metrics(2.0, 0.05, 0.0))}
    assert all(v for k, v in ok.items() if k.startswith("PERF-04"))
    assert not any("hit" in k for k in ok)
    bad = {c["id"]: c["ok"] for c in bench.checks(metrics(3.5, 0.25, 0.08))}
    assert [k for k, v in bad.items() if k.startswith("PERF-04") and not v and "single-flight" not in k] == [
        "PERF-04 upstream team-id lookups per team-only viewer per minute <= 3",
        "PERF-04 team-id miss p95 < 100 ms",
        "PERF-04 team-id cache error ratio <= 5%",
    ]


def test_upstream_buckets_parse_prom_client_label_order() -> None:
    """prom-client serialises `le` before the other labels; both results are summed per bound."""
    text = "\n".join([
        "# TYPE collab_teamids_upstream_seconds histogram",
        'collab_teamids_upstream_seconds_bucket{le="0.005",result="ok"} 3',
        'collab_teamids_upstream_seconds_bucket{le="0.1",result="ok"} 9',
        'collab_teamids_upstream_seconds_bucket{le="+Inf",result="ok"} 10',
        'collab_teamids_upstream_seconds_bucket{result="error",le="0.1"} 1',
        'collab_teamids_upstream_seconds_count{result="ok"} 10',
    ])
    buckets = bench.parse_upstream_buckets(text)
    assert buckets == {0.005: 3.0, 0.1: 10.0, float("inf"): 10.0}
    assert bench.bucket_percentile({}, buckets, 95) == 0.1
